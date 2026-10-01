#!/usr/bin/env python3
"""
Monitor de cargas VOIP — Bitrix24 (polling) + OCR local.

Revisa periódicamente los mensajes nuevos del chat de Bitrix24 configurado
en la pestaña "Cargas VOIP" de la aplicación. Cuando encuentra un mensaje
del tipo "CARGA $100.000 TRM 3.126.08" con una imagen adjunta:

  1. Lee el monto en pesos y la TRM DECLARADOS, directo del texto del
     mensaje (viene limpio desde la API, no hace falta OCR para esto).
  2. Descarga la imagen adjunta y usa OCR local (Tesseract, gratis, sin
     API externa) para leer el monto en USD que REALMENTE quedó
     registrado en el sistema (lo que muestra la captura).
  3. Compara pesos ÷ TRM (lo declarado) contra el USD real leído de la
     imagen, con un margen de tolerancia.
  4. Verifica que la TRM declarada coincida con la TRM oficial del día
     publicada por la Superintendencia Financiera de Colombia.
  5. Responde en el mismo chat con ✅ o ⚠️, y deja todo en un CSV para
     auditoría.

Adaptado del proyecto CARGAS_VOIP para integrarse en Saldos Scheduler:
la configuración se lee de 'scheduler_config.json' (clave
'cargas_voip_config'), editable desde la pestaña "Cargas VOIP" de la GUI.
Se ejecuta como subproceso en segundo plano, igual que el planificador de
saldos.

El equipo usa este monitor rotando entre 3 PC (no al tiempo): cada una lo
corre una temporada y lo deja quieto mientras otra lo usa. El progreso
("hasta qué mensaje ya se revisó", el 'last_id') NO puede vivir solo en
el archivo local de cada PC (estado_cargas_voip.json) -si viviera ahí,
la PC que retoma el monitor después de varias semanas tendría un last_id
viejo y reprocesaría cargas que otra PC ya validó mientras tanto-. Por
eso el last_id también se guarda en una hoja de Google Sheets COMPARTIDA
entre las 3 (ver ESTADO_SHEET_URL_DEFAULT / _leer_last_id_compartido /
_guardar_last_id_compartido): al arrancar, se toma el mayor entre el
local y el de la hoja, y tras cada tanda de mensajes nuevos se guarda en
ambos. El archivo local sigue existiendo como respaldo (si Sheets no
responde un rato, el monitor no se detiene, solo pierde por ese rato la
sincronización entre PC).

Como red de seguridad adicional (por si algún mensaje se coló sin
confirmar -ej. un corte justo entre procesarlo y guardar el estado-), en
cada ciclo también se revisan los últimos "lookback_n" mensajes ANTERIORES
a last_id (10 por defecto): si alguno es una "CARGA..." que no consta como
ya confirmado, se reprocesa igual que uno nuevo. "¿Ya confirmado?" se
responde con dos registros que sí son confiables porque los escribe este
mismo código (se probó en producción que Bitrix NO devuelve el REPLY_ID
de la respuesta en im.dialog.messages.get, así que esa detección no sirve):
el CSV local de auditoría (para "esta misma PC ya lo procesó") y una lista
de "confirmados recientes" en la misma hoja de Sheets del last_id (para
"otra PC ya lo confirmó"). Ver _cargar_ids_registrados_localmente(),
_leer_confirmados_compartidos() y la "ventana de reintento" dentro de run().

Además de esos dos registros, existe un tercer caso: un compañero revisa la
carga A SIMPLE VISTA (sin correr este programa) y escribe "confirmada" /
"CONFIRMADA" / "confirmadas" directo en el chat. Eso no queda en el CSV ni
en Sheets, así que antes de procesar una carga también se revisa si, en la
misma tanda de mensajes, ya tiene una de esas confirmaciones manuales
DESPUÉS en el chat -si la tiene, se omite (solo se deja constancia en el
CSV como CONFIRMADO_MANUAL); si no, se procesa normal-. Ver
CONFIRMACION_MANUAL_RE, _ids_confirmados_manualmente() y
_registrar_confirmacion_manual().

También existe un modo de prueba ("dry_run" en la config, casilla "Modo de
prueba" en la pestaña), igual que en didww_renovacion.py: con él activado
(que es el valor por defecto) el monitor analiza los mensajes y registra en
el log qué haría, pero NUNCA escribe en el chat de Bitrix24 ni toca ningún
estado persistente (CSV de auditoría, estado local, last_id/confirmados
compartidos en Sheets) -así se puede calibrar la detección con el historial
real sin que el equipo vea mensajes de prueba ni se entere si algo sale
mal-. Ver _responder_o_loguear() y _registrar_o_loguear().
"""
import os
import re
import csv
import sys
import json
import time
import io
import socket
import logging
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests
import pytesseract
from PIL import Image, ImageOps, ImageFilter

if getattr(sys, 'frozen', False):
    BASE_DIR = Path(sys.executable).resolve().parent
else:
    BASE_DIR = Path(__file__).resolve().parent

from core.logging_tools import configurar_logger_rotativo

CONFIG_FILE = BASE_DIR / "scheduler_config.json"
STATE_FILE = BASE_DIR / "estado_cargas_voip.json"
CSV_FILE = BASE_DIR / "registro_cargas.csv"
LOG_FILE = BASE_DIR / "cargas_voip.log"

TRM_SOAP_URL = "https://www.superfinanciera.gov.co/SuperfinancieraWebServiceTRM/TCRMServicesWebService/TCRMServicesWebService"
# Colombia no usa horario de verano, un offset fijo alcanza.
BOGOTA_TZ = timezone(timedelta(hours=-5))
_trm_oficial_cache = {}

# Hoja de Sheets dedicada solo a guardar el last_id compartido entre las 3
# PC (no es la hoja de saldos). Configurable desde la pestaña "Cargas VOIP"
# ("estado_sheet_url"); este es el valor por defecto que se usa si no se
# configuró otra.
ESTADO_SHEET_URL_DEFAULT = "https://docs.google.com/spreadsheets/d/1ql2y9RBBS4aelGIIawJNielWYo_BUwxtNRCdvVy_gac/edit?gid=0"
_GOOGLE_SHEETS_SCOPE = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]

# "CARGA $100.000 TRM 3.126.08" / "CARGA DE $250.000 TRM 3.126.08" / "carga
# de saldo 100.000 trm 3.306.86" / "Carga de saldo por $2.000.000 trm
# 3.151.73" -el equipo no siempre escribe "CARGA [DE] $monto", a veces mete
# palabras de relleno como "de saldo" o "de saldo por" en el medio-. En vez
# de enumerar cada frase posible, se salta cualquier cosa que no sea un
# dígito o "$" hasta encontrar el monto -sigue exigiendo que el mensaje
# arranque con "CARGA" y que en algún punto después aparezca "TRM <número>",
# así que no hay riesgo de confundir un mensaje que no sea de carga-.
CARGA_RE = re.compile(r"^\s*CARGA\b[^$0-9]*\$?\s*([\d.,]+)\s*TRM\s*([\d.,]+)", re.IGNORECASE)
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
AMOUNT_CANDIDATE_RE = re.compile(r"[\d]{1,3}(?:[.,]\d{3})*[.,]\d{2,3}")

# Confirmación escrita A MANO por un compañero en el chat (sin usar el
# programa): mensaje corto que es solo esta palabra ("confirmada",
# "CONFIRMADA", "confirmadas", "confirmado"...). Se exige que sea TODO el
# mensaje (con como mucho un punto/exclamación al final) para no confundirla
# jamás con la propia respuesta del bot, que siempre trae un emoji y más
# texto (ej. "✅ Monto confirmado: $100.000 COP a TRM 3.306,86 = USD 30,25").
CONFIRMACION_MANUAL_RE = re.compile(r"^\s*confirmad[oa]s?\s*[.!]?\s*$", re.IGNORECASE)

DEFAULT_CARGAS_VOIP_CONFIG = {
    "bitrix_webhook_url": "",
    "chat_id": "chat135",
    "poll_interval": 20,
    "tolerancia_pct": 0.5,
    "tolerancia_trm_pct": 0.1,
    "tesseract_cmd": "",
    "debug_ocr": True,
    "estado_sheet_url": ESTADO_SHEET_URL_DEFAULT,
    # Cuántos mensajes ANTERIORES a last_id se revisan en cada ciclo por si
    # alguno se quedó sin confirmar (ver "ventana de reintento" en run()).
    "lookback_n": 10,
    # Modo de prueba (mismo patrón que didww_renovacion.py): nunca escribe
    # en el chat de Bitrix24 ni modifica ningún estado persistente (CSV de
    # auditoría, estado local, last_id/confirmados compartidos en Sheets) -
    # todo lo que haría queda solo en el log, para poder calibrar sin que
    # el equipo vea mensajes de prueba ni se entere si algo sale mal.
    "dry_run": True,
}

logger = logging.getLogger("cargas_voip")


def _setup_logging(debug: bool):
    # Rota el log a la medianoche: cada día arranca vacío y solo se
    # conservan los últimos días (backupCount) en archivos aparte
    # ('cargas_voip.log.YYYY-MM-DD'), que se van borrando solos - así no
    # se acumula información indefinidamente. El CSV de auditoría
    # ('registro_cargas.csv') NO se toca: ese es el registro permanente.
    # (La rotación en sí vive en core/logging_tools.py -no alcanza con el
    # TimedRotatingFileHandler solo, porque este monitor se prende y apaga
    # de forma intermitente; ver el docstring de ese módulo.)
    configurar_logger_rotativo(logger, LOG_FILE, backup_count=3,
                                nivel=logging.DEBUG if debug else logging.INFO)


def load_cargas_voip_config() -> dict:
    """Combina, en orden de prioridad creciente: valores por defecto,
    variables de entorno (heredadas del script original CARGAS_VOIP, útiles
    como pre-relleno la primera vez) y por último lo guardado desde la
    pestaña "Cargas VOIP" de la GUI en scheduler_config.json, que es lo que
    manda una vez el usuario guarda su configuración."""
    cfg = DEFAULT_CARGAS_VOIP_CONFIG.copy()

    if os.environ.get("BITRIX_WEBHOOK_URL"):
        cfg["bitrix_webhook_url"] = os.environ["BITRIX_WEBHOOK_URL"]
    if os.environ.get("CHAT_ID"):
        cfg["chat_id"] = os.environ["CHAT_ID"]
    if os.environ.get("POLL_INTERVAL_SECONDS"):
        cfg["poll_interval"] = int(os.environ["POLL_INTERVAL_SECONDS"])
    if os.environ.get("TOLERANCIA_PORCENTAJE"):
        cfg["tolerancia_pct"] = float(os.environ["TOLERANCIA_PORCENTAJE"])
    if os.environ.get("TOLERANCIA_TRM_PORCENTAJE"):
        cfg["tolerancia_trm_pct"] = float(os.environ["TOLERANCIA_TRM_PORCENTAJE"])
    if os.environ.get("DEBUG_OCR"):
        cfg["debug_ocr"] = os.environ["DEBUG_OCR"] == "1"
    if os.environ.get("TESSERACT_CMD"):
        cfg["tesseract_cmd"] = os.environ["TESSERACT_CMD"]

    cfg["credentials_path"] = str(BASE_DIR / "credenciales.json")

    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8-sig") as f:
                full_cfg = json.load(f)
            for k, v in full_cfg.get("cargas_voip_config", {}).items():
                if v not in ("", None):
                    cfg[k] = v
            # credentials_path es de nivel raíz (lo comparte toda la app,
            # no es propio de "cargas_voip_config"): se necesita para
            # autenticar contra la hoja de Sheets del last_id compartido.
            if full_cfg.get("credentials_path"):
                cfg["credentials_path"] = full_cfg["credentials_path"]
        except Exception:
            pass

    return cfg


# ---------------------------------------------------------------------------
# TRM oficial (Superintendencia Financiera de Colombia)
# ---------------------------------------------------------------------------
def _obtener_trm_oficial(fecha):
    fecha_str = fecha.strftime("%Y-%m-%d")
    if fecha_str in _trm_oficial_cache:
        return _trm_oficial_cache[fecha_str]

    soap = f"""<?xml version="1.0" encoding="utf-8"?>
<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/"
                  xmlns:ws="http://action.trm.services.generic.action.superfinanciera.nexura.sc.com.co/">
    <soapenv:Header/>
    <soapenv:Body>
        <ws:queryTCRM>
            <tcrmQueryAssociatedDate>{fecha_str}</tcrmQueryAssociatedDate>
        </ws:queryTCRM>
    </soapenv:Body>
</soapenv:Envelope>"""

    try:
        resp = requests.post(
            TRM_SOAP_URL,
            data=soap.encode("utf-8"),
            headers={"Content-Type": "text/xml; charset=utf-8", "SOAPAction": ""},
            timeout=20,
        )
        resp.raise_for_status()
        root = ET.fromstring(resp.content)

        value_node = None
        for elem in root.iter():
            if elem.tag.split("}")[-1] == "value":
                value_node = elem
                break

        if value_node is None or not value_node.text:
            raise ValueError(f"No se encontró el nodo <value> en la respuesta SOAP: {resp.text[:500]}")

        trm = float(value_node.text.strip())
    except Exception as e:
        logger.debug(f"No se pudo obtener la TRM oficial para {fecha_str}: {e}")
        return None

    _trm_oficial_cache[fecha_str] = trm
    return trm


def _verificar_trm(trm_declarada, fecha_mensaje, tolerancia_trm_pct):
    trm_oficial = _obtener_trm_oficial(fecha_mensaje)
    if trm_oficial is None:
        return {"trm_oficial": None, "trm_ok": None, "diferencia_trm_pct": None}

    diferencia_trm_pct = abs(trm_declarada - trm_oficial) / trm_oficial * 100
    return {
        "trm_oficial": trm_oficial,
        "diferencia_trm_pct": round(diferencia_trm_pct, 3),
        "trm_ok": diferencia_trm_pct <= tolerancia_trm_pct,
    }


def _fecha_del_mensaje(msg):
    """Bitrix24 informa la fecha del mensaje en la zona horaria de la
    cuenta, no en hora de Colombia; hay que convertir antes de sacar el
    día calendario (relevante cerca de la medianoche)."""
    try:
        dt = datetime.fromisoformat(msg["date"])
        return dt.astimezone(BOGOTA_TZ).date()
    except Exception:
        return datetime.now(BOGOTA_TZ).date()


# ---------------------------------------------------------------------------
# Utilidades de parseo de números
# ---------------------------------------------------------------------------
def _parsear_numero_latino(s):
    """Pesos como '100.000' (todos los puntos son miles), o TRM como
    '3.126.08' (el último punto es decimal). Se distinguen por la
    cantidad de dígitos del último grupo."""
    s = s.strip()
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".")
        return float(s)
    if "," in s:
        return float(s.replace(",", "."))

    partes = s.split(".")
    if len(partes) == 1:
        return float(partes[0])

    ultimo = partes[-1]
    if len(ultimo) == 3:
        return float("".join(partes))
    return float("".join(partes[:-1]) + "." + ultimo)


def _parsear_usd(s):
    return float(s.strip().replace(",", "."))


# ---------------------------------------------------------------------------
# Estado local (para no reprocesar mensajes ya vistos)
# ---------------------------------------------------------------------------
def _cargar_estado():
    if STATE_FILE.exists():
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"last_id": 0}


def _guardar_estado(estado):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(estado, f)


# ---------------------------------------------------------------------------
# Estado COMPARTIDO (Google Sheets): el last_id real -el que importa- es el
# mayor entre el de esta PC y el de la hoja, así que si otra PC avanzó más
# mientras esta estaba apagada, se respeta ese avance en vez de reprocesar
# todo lo que ya validaron. Ver el docstring del módulo para el porqué.
# ---------------------------------------------------------------------------
def _abrir_hoja_estado(credentials_path, sheet_url):
    from oauth2client.service_account import ServiceAccountCredentials
    import gspread
    creds = ServiceAccountCredentials.from_json_keyfile_name(credentials_path, _GOOGLE_SHEETS_SCOPE)
    client = gspread.authorize(creds)
    return client.open_by_url(sheet_url).sheet1


def _leer_last_id_compartido(credentials_path, sheet_url):
    """None si no se pudo leer (sin credenciales, sin internet, hoja no
    compartida con la cuenta de servicio, etc.) -en ese caso el monitor
    sigue funcionando solo con el estado local, como antes-."""
    if not credentials_path or not sheet_url:
        return None
    try:
        hoja = _abrir_hoja_estado(credentials_path, sheet_url)
        valor = (hoja.acell("A2").value or "").strip()
        return int(valor) if valor else 0
    except Exception as e:
        logger.warning(f"No se pudo leer el last_id compartido de Sheets ({e}); se sigue solo con el estado local.")
        return None


def _guardar_last_id_compartido(credentials_path, sheet_url, last_id):
    if not credentials_path or not sheet_url:
        return
    try:
        hoja = _abrir_hoja_estado(credentials_path, sheet_url)
        # gspread 6.x espera (values, range_name) -en ese orden-; se pasan
        # como keywords para no depender del orden posicional si alguna
        # vez cambia de versión otra vez (ya cambió una vez, de hecho).
        hoja.update(
            range_name="A1:C2",
            values=[
                ["last_id", "actualizado_por", "fecha"],
                [str(last_id), socket.gethostname(), datetime.now(BOGOTA_TZ).strftime("%Y-%m-%d %H:%M:%S")],
            ],
        )
    except Exception as e:
        logger.warning(f"No se pudo guardar el last_id compartido en Sheets ({e}); "
                        "el progreso queda solo local hasta que se pueda sincronizar de nuevo.")


# ---------------------------------------------------------------------------
# Llamadas a Bitrix24
# ---------------------------------------------------------------------------
def _obtener_mensajes(webhook_url, chat_id):
    resp = requests.get(
        f"{webhook_url}im.dialog.messages.get",
        params={"DIALOG_ID": chat_id, "LIMIT": 50},
        timeout=20,
    )
    resp.raise_for_status()
    result = resp.json().get("result", {})
    return result.get("messages", []), result.get("files", {})


def _responder(webhook_url, chat_id, texto, reply_id=None):
    payload = {"DIALOG_ID": chat_id, "MESSAGE": texto}
    if reply_id is not None:
        payload["REPLY_ID"] = reply_id
    requests.post(f"{webhook_url}im.message.add", json=payload, timeout=15)


def _responder_o_loguear(webhook_url, chat_id, texto, msg_id, dry_run):
    """En modo de prueba NUNCA se escribe en el chat de Bitrix24 -para poder
    calibrar la detección y el OCR sin que el equipo vea mensajes de prueba
    ni se entere si algo sale mal-; el mensaje que se habría enviado queda
    solo en el log (mismo criterio que didww_renovacion.py)."""
    if dry_run:
        logger.info(f"[PRUEBA] No se envía al chat (modo de prueba). Respuesta que se habría dado "
                    f"al mensaje {msg_id}:\n{texto}")
        return
    _responder(webhook_url, chat_id, texto, reply_id=msg_id)


def _registrar_o_loguear(pesos, trm, usd_real, resultado, trm_check, msg_id, dry_run):
    """Contraparte de _registrar() para modo de prueba: no toca el CSV real
    de auditoría (se mezclarían filas de prueba con las reales) — el
    resultado que se habría guardado queda solo en el log."""
    if dry_run:
        logger.info(f"[PRUEBA] No se registra en '{CSV_FILE.name}' (modo de prueba). Resultado que se habría "
                    f"guardado: mensaje={msg_id}, estado={resultado.get('estado')}, pesos={pesos}, trm={trm}, "
                    f"usd_real={usd_real}")
        return
    _registrar(pesos, trm, usd_real, resultado, trm_check, msg_id)


# ---------------------------------------------------------------------------
# OCR de la imagen adjunta
# ---------------------------------------------------------------------------
def _preprocesar_imagen(image_bytes):
    img = Image.open(io.BytesIO(image_bytes)).convert("L")
    img = ImageOps.autocontrast(img)

    ancho, alto = img.size
    factor = max(1, 1400 / ancho, 150 / alto)
    if factor > 1:
        img = img.resize((int(ancho * factor), int(alto * factor)), Image.LANCZOS)

    return img.filter(ImageFilter.SHARPEN)


def _ocr_texto(img, psm):
    return pytesseract.image_to_string(img, lang="spa+eng", config=f"--psm {psm}")


# Estas capturas son tiras de una sola fila de tabla, con columnas separadas
# por huecos anchos de espacio en blanco y letra pequeña. Ningún modo de
# segmentación de página (PSM) de Tesseract es fiable para todas: probamos
# varios en orden y usamos el primero que dé un candidato, salvo que
# tengamos con qué contrastarlo (ver más abajo).
#
# 8 y 13 van primero porque, en las pruebas hechas, son los más fiables para
# el dígito inicial de este tipo de captura (evita que un candidato mal
# leído por un modo anterior quede como "primer_candidato" — el que se
# muestra en el mensaje de "revisar" cuando ningún candidato coincide con
# el monto esperado, por ejemplo si la TRM del mensaje viene mal tipeada).
OCR_PSM_MODES = (8, 13, 6, 11, 4, 3, 12, 7)


def _extraer_usd_de_imagen(image_bytes, usd_esperado=None, tolerancia_pct=0.5, debug=False):
    """Prueba varios modos de PSM sobre la misma imagen. Si se conoce el
    monto USD esperado (pesos / TRM, ya sacado del texto del mensaje), se
    devuelve el primer candidato que caiga dentro de la tolerancia — así,
    aunque un modo concreto desfigure los dígitos (ej. lee "63.978" como
    "3.973"), alcanza con que UNO de los modos lo lea bien para confirmar
    el monto. Si ninguno coincide (o no hay con qué comparar), se devuelve
    el primer candidato encontrado, para que el mensaje de "revisar" siga
    mostrando una lectura real de la imagen."""
    img = _preprocesar_imagen(image_bytes)

    primer_candidato = None
    for psm in OCR_PSM_MODES:
        texto = _ocr_texto(img, psm=psm)
        if debug:
            logger.debug("----- TEXTO OCR CRUDO (psm %s) -----\n%s\n----------------------------", psm, texto)

        fecha_match = DATE_RE.search(texto)
        prefijo = texto[:fecha_match.start()] if fecha_match else texto

        candidatos = AMOUNT_CANDIDATE_RE.findall(prefijo)
        if debug:
            logger.debug(f"Candidatos a monto USD (antes de la fecha, psm {psm}): {candidatos}")

        if not candidatos:
            continue

        if primer_candidato is None:
            primer_candidato = _parsear_usd(candidatos[-1])

        if usd_esperado:
            # No alcanza con mirar solo el último candidato: si la fecha no
            # se reconoció bien (ej. el OCR mezcló el día con la hora, como
            # "2026-09-1 515:49:38"), la "prefijo" queda con toda la
            # descripción del mensaje y el monto en pesos de ahí (ej.
            # "$200.000 PESOS") puede colarse después del monto USD real,
            # tapándolo si solo se prueba el último. Se prueban todos los
            # candidatos de esta pasada contra lo esperado.
            for candidato in candidatos:
                valor = _parsear_usd(candidato)
                diferencia_pct = abs(valor - usd_esperado) / usd_esperado * 100
                if diferencia_pct <= tolerancia_pct:
                    if debug:
                        logger.debug(f"Candidato de psm {psm} coincide con lo esperado ({usd_esperado}): {valor}")
                    return valor

    return primer_candidato


# ---------------------------------------------------------------------------
# Validación: lo declarado (texto) vs. lo real (imagen)
# ---------------------------------------------------------------------------
def _validar(pesos, trm, usd_real, tolerancia_pct):
    if usd_real is None:
        return {"estado": "REVISAR_MANUAL", "motivo": "No se pudo leer el monto USD en la imagen"}
    usd_esperado = pesos / trm
    diferencia_pct = abs(usd_esperado - usd_real) / usd_esperado * 100
    estado = "CONFIRMADO" if diferencia_pct <= tolerancia_pct else "REVISAR"
    return {"estado": estado, "usd_esperado": round(usd_esperado, 3), "diferencia_pct": round(diferencia_pct, 3)}


def _registrar(pesos, trm, usd_real, resultado, trm_check, msg_id):
    """Escribe la fila en el CSV de auditoría. Si el archivo está bloqueado
    (por ejemplo porque alguien lo tiene abierto en Excel), reintenta unas
    cuantas veces antes de rendirse — sin esto, un bloqueo momentáneo hacía
    perder para siempre el registro de ese mensaje."""
    fila = [datetime.now().isoformat(), msg_id, pesos, trm, usd_real,
            resultado["estado"], resultado.get("diferencia_pct"),
            trm_check.get("trm_oficial"), trm_check.get("trm_ok"),
            trm_check.get("diferencia_trm_pct")]

    intentos = 5
    for intento in range(1, intentos + 1):
        try:
            nuevo = not CSV_FILE.exists()
            with open(CSV_FILE, "a", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                if nuevo:
                    writer.writerow(["timestamp", "mensaje_id", "monto_pesos_declarado", "trm_declarada",
                                      "usd_real_en_imagen", "estado", "diferencia_pct",
                                      "trm_oficial", "trm_ok", "diferencia_trm_pct"])
                writer.writerow(fila)
            return
        except PermissionError as e:
            if intento < intentos:
                logger.warning(f"'{CSV_FILE.name}' está bloqueado (¿abierto en Excel?), reintentando ({intento}/{intentos})...")
                time.sleep(2)
            else:
                logger.error(f"No se pudo escribir en '{CSV_FILE.name}' tras {intentos} intentos "
                             f"(ciérralo si lo tienes abierto en Excel u otro programa): {e}")


# ---------------------------------------------------------------------------
# Ventana de reintento: ¿ya se confirmó este mensaje?
#
# Probado en producción: Bitrix NO devuelve el REPLY_ID en
# im.dialog.messages.get aunque _responder() se lo mande a im.message.add
# al contestar -así que esa detección nunca encontraba nada y reprocesaba
# TODO lo que entraba en la ventana en cada reinicio-. En su lugar se usan
# dos fuentes que sí son confiables porque las escribe este mismo código:
#   1. El CSV local (registro_cargas.csv): cubre el caso más común -esta
#      misma PC ya lo procesó, aunque el proceso se haya reiniciado justo
#      después- sin depender de nada externo.
#   2. Una lista de "confirmados recientes" en la MISMA hoja de Sheets
#      compartida del last_id (ver _leer_confirmados_compartidos): cubre
#      el caso de que OTRA PC lo haya confirmado mientras esta no corría.
# ---------------------------------------------------------------------------
def _cargar_ids_registrados_localmente():
    """ids de mensajes que este PC ya dejó en el CSV de auditoría -se lee
    una sola vez al arrancar run(); de ahí en más se actualiza en memoria
    a medida que se procesan mensajes, para no releer el archivo entero
    en cada ciclo-.

    Se lee por POSICIÓN de columna (mensaje_id es la 2da, índice 1), no
    por nombre de encabezado vía csv.DictReader: el CSV real del equipo
    arrastra un encabezado viejo separado por ";" mientras que todas las
    filas de datos (las que escribe _registrar) usan ",", así que
    DictReader nunca encuentra una columna "mensaje_id" -junta todo el
    encabezado en un solo nombre de campo- y esta función devolvía
    siempre un set vacío sin que ningún error se notara (quedaba
    silenciado). Leer por posición no depende para nada del encabezado."""
    ids = set()
    if not CSV_FILE.exists():
        return ids
    try:
        with open(CSV_FILE, "r", newline="", encoding="utf-8") as f:
            filas = csv.reader(f)
            next(filas, None)  # se salta el encabezado, sea cual sea su formato
            for fila in filas:
                if len(fila) < 2:
                    continue
                try:
                    ids.add(int(fila[1].strip()))
                except (ValueError, TypeError):
                    continue
    except Exception as e:
        logger.warning(f"No se pudo leer '{CSV_FILE.name}' para la ventana de reintento: {e}")
    return ids


def _leer_confirmados_compartidos(credentials_path, sheet_url):
    if not credentials_path or not sheet_url:
        return set()
    try:
        hoja = _abrir_hoja_estado(credentials_path, sheet_url)
        valor = (hoja.acell("B4").value or "").strip()
        return {int(x) for x in valor.split(",") if x.strip().isdigit()} if valor else set()
    except Exception as e:
        logger.warning(f"No se pudo leer la lista de confirmados compartida ({e}); "
                        "se sigue solo con el registro local para la ventana de reintento.")
        return set()


def _guardar_confirmados_compartidos(credentials_path, sheet_url, ids, tope=200):
    """Guarda como máximo los `tope` ids más altos -alcanza de sobra para
    cualquier lookback_n razonable y evita que la celda crezca sin límite
    con meses de uso-."""
    if not credentials_path or not sheet_url:
        return
    try:
        hoja = _abrir_hoja_estado(credentials_path, sheet_url)
        recientes = sorted(ids)[-tope:]
        hoja.update(
            range_name="A4:B4",
            values=[["confirmados_recientes", ",".join(str(i) for i in recientes)]],
        )
    except Exception as e:
        logger.warning(f"No se pudo guardar la lista de confirmados compartida ({e}).")


# ---------------------------------------------------------------------------
# Confirmación manual en el chat (sin pasar por el programa)
#
# El equipo rota este monitor entre 3 PC, no siempre corriendo: mientras
# nadie lo tiene encendido, un compañero puede revisar una carga a simple
# vista y escribir "confirmada"/"CONFIRMADA"/"confirmadas" directo en el
# chat. Eso NO actualiza el last_id (solo lo actualiza este programa), así
# que si luego se retoma el monitor con un last_id viejo, se reprocesarían
# cargas que ya quedaron validadas a mano -confirmándolas otra vez y
# generando ruido en el chat-. Por eso, antes de decidir si una carga hay
# que procesarla, se revisa si ya tiene una confirmación manual después en
# el mismo lote de mensajes: si la tiene, se omite (solo se deja constancia
# en el registro); si no, se procesa normal.
# ---------------------------------------------------------------------------
def _ids_confirmados_manualmente(mensajes_asc):
    """A partir de una lista de mensajes en orden ASCENDENTE de id, devuelve
    los ids de los mensajes de "CARGA..." que ya tienen una confirmación
    manual (CONFIRMACION_MANUAL_RE) después en el chat. Una sola
    confirmación puede cubrir varias cargas seguidas -el equipo a veces
    escribe "confirmadas" una sola vez para un lote-, así que las cargas
    pendientes se van acumulando y se marcan todas como confirmadas en
    cuanto aparece la palabra."""
    confirmados = set()
    pendientes = []
    for m in mensajes_asc:
        texto = m.get("text", "") or ""
        if CONFIRMACION_MANUAL_RE.match(texto):
            confirmados.update(pendientes)
            pendientes = []
        elif CARGA_RE.search(texto):
            pendientes.append(int(m["id"]))
    return confirmados


def _registrar_confirmacion_manual(msg, cfg):
    """Un compañero ya confirmó esta carga a mano en el chat -no hace falta
    OCR ni respuesta del bot, ya está confirmada-; solo se deja constancia
    en el CSV de auditoría (estado CONFIRMADO_MANUAL) para que la ventana de
    reintento no la vuelva a tocar. En modo de prueba no se escribe nada,
    igual que el resto del procesamiento."""
    dry_run = bool(cfg.get("dry_run", True))
    texto = msg.get("text", "") or ""
    msg_id = msg.get("id")
    m = CARGA_RE.search(texto)
    if not m:
        return

    pesos = _parsear_numero_latino(m.group(1))
    trm = _parsear_numero_latino(m.group(2))
    sin_trm_check = {"trm_oficial": None, "trm_ok": None, "diferencia_trm_pct": None}

    logger.info(f"Mensaje {msg_id} (\"{texto}\") ya fue confirmado a mano en el chat; "
                "se omite el procesamiento automático." + (" [PRUEBA]" if dry_run else ""))
    _registrar_o_loguear(pesos, trm, None, {"estado": "CONFIRMADO_MANUAL"}, sin_trm_check, msg_id, dry_run)


# ---------------------------------------------------------------------------
# Procesar un mensaje nuevo
# ---------------------------------------------------------------------------
def _obtener_file_ids(msg):
    params = msg.get("params")
    if isinstance(params, dict):
        ids = params.get("FILE_ID")
        if ids:
            return ids if isinstance(ids, list) else [ids]

    file_obj = msg.get("file")
    if isinstance(file_obj, dict) and file_obj.get("id"):
        return [file_obj["id"]]

    return []


def _obtener_url_descarga(webhook_url, file_id):
    resp = requests.get(f"{webhook_url}disk.file.get", params={"id": file_id}, timeout=20)
    resp.raise_for_status()
    data = resp.json()
    if "error" in data:
        raise RuntimeError(data.get("error_description", data["error"]))
    return data["result"]["DOWNLOAD_URL"]


def _procesar_mensaje(msg, cfg):
    webhook_url = cfg["bitrix_webhook_url"]
    chat_id = cfg["chat_id"]
    debug = cfg["debug_ocr"]
    dry_run = bool(cfg.get("dry_run", True))

    params = msg.get("params")
    if isinstance(params, dict) and params.get("IS_DELETED") == "Y":
        if debug:
            logger.debug(f"[mensaje {msg.get('id')}] eliminado, se ignora.")
        return

    texto = msg.get("text", "") or ""
    m = CARGA_RE.search(texto)
    if debug:
        logger.debug(f"[mensaje {msg.get('id')}] texto={texto!r} -> "
                      f"{'COINCIDE con patrón CARGA' if m else 'NO coincide con el patrón CARGA'}")

    if not m:
        return  # no es un mensaje de carga, se ignora

    pesos = _parsear_numero_latino(m.group(1))
    trm = _parsear_numero_latino(m.group(2))
    msg_id = msg.get("id")
    fecha_msg = _fecha_del_mensaje(msg)

    trm_check = _verificar_trm(trm, fecha_msg, cfg["tolerancia_trm_pct"])
    if debug:
        logger.debug(f"Verificación TRM oficial: {trm_check}")

    file_ids = _obtener_file_ids(msg)
    if not file_ids:
        _responder_o_loguear(webhook_url, chat_id, f"⚠️ Revisar manual: el mensaje \"{texto}\" no trae imagen adjunta.", msg_id, dry_run)
        _registrar_o_loguear(pesos, trm, None, {"estado": "REVISAR_MANUAL"}, trm_check, msg_id, dry_run)
        return

    try:
        url_descarga = _obtener_url_descarga(webhook_url, file_ids[0])
    except Exception as e:
        logger.warning(f"Error obteniendo DOWNLOAD_URL: {e}")
        _responder_o_loguear(webhook_url, chat_id, f"⚠️ Revisar manual: no pude obtener el archivo adjunto de \"{texto}\".", msg_id, dry_run)
        _registrar_o_loguear(pesos, trm, None, {"estado": "REVISAR_MANUAL"}, trm_check, msg_id, dry_run)
        return

    img_resp = requests.get(url_descarga, timeout=30)
    img_resp.raise_for_status()

    usd_esperado = pesos / trm
    usd_real = _extraer_usd_de_imagen(
        img_resp.content, usd_esperado=usd_esperado, tolerancia_pct=cfg["tolerancia_pct"], debug=debug
    )
    resultado = _validar(pesos, trm, usd_real, cfg["tolerancia_pct"])

    lineas = []
    if resultado["estado"] == "CONFIRMADO":
        lineas.append(f"✅ Monto confirmado: ${pesos:,.0f} COP a TRM {trm} = USD {resultado['usd_esperado']}")
    elif resultado["estado"] == "REVISAR_MANUAL":
        lineas.append(f"⚠️ Revisar manual: no pude leer el monto USD en la imagen de \"{texto}\".")
    else:
        lineas.append(f"⚠️ Revisar monto: \"{texto}\" — esperado = USD {resultado['usd_esperado']}, "
                       f"la imagen muestra USD {usd_real} (diferencia {resultado['diferencia_pct']}%)")

    if trm_check["trm_ok"] is False:
        lineas.append(f"⚠️ TRM declarada ({trm}) no coincide con la oficial del día "
                       f"({trm_check['trm_oficial']}), diferencia {trm_check['diferencia_trm_pct']}%.")
    elif trm_check["trm_ok"] is None:
        lineas.append("ℹ️ No se pudo verificar la TRM oficial del día (revisar manualmente si hace falta).")

    _responder_o_loguear(webhook_url, chat_id, "\n".join(lineas), msg_id, dry_run)
    _registrar_o_loguear(pesos, trm, usd_real, resultado, trm_check, msg_id, dry_run)
    logger.info(f"Mensaje {msg_id} procesado -> {resultado['estado']}" + (" [PRUEBA]" if dry_run else ""))


# ---------------------------------------------------------------------------
# Loop principal
# ---------------------------------------------------------------------------
def run(cfg: dict = None, stop_event=None):
    """Punto de entrada del monitor. Se lanza como subproceso en segundo
    plano desde la pestaña "Cargas VOIP" de la GUI, igual que el
    planificador de saldos. `stop_event` (threading.Event) es opcional,
    solo se usa si en algún momento se embebe en un hilo en vez de un
    subproceso aparte."""
    cfg = cfg or load_cargas_voip_config()
    _setup_logging(cfg.get("debug_ocr", False))

    if cfg.get("tesseract_cmd"):
        pytesseract.pytesseract.tesseract_cmd = cfg["tesseract_cmd"]

    if not cfg.get("bitrix_webhook_url"):
        logger.error("bitrix_webhook_url no configurado.")
        print("ERROR: falta configurar la URL del webhook de Bitrix24 (pestaña 'Cargas VOIP').", flush=True)
        return

    dry_run = bool(cfg.get("dry_run", True))
    if dry_run:
        logger.info("Modo de prueba (dry-run) ACTIVADO: no se enviará nada al chat de Bitrix24, ni se modificará "
                     "el CSV de auditoría, ni el estado local/compartido (last_id y confirmados en Sheets); "
                     "todo lo que haría el monitor queda solo registrado en este log.")

    estado = _cargar_estado()
    chat_id = cfg["chat_id"]
    poll_interval = int(cfg.get("poll_interval", 20))
    credentials_path = cfg.get("credentials_path", "")
    estado_sheet_url = cfg.get("estado_sheet_url", "") or ESTADO_SHEET_URL_DEFAULT

    # El last_id que realmente importa es el MAYOR entre el local (esta PC)
    # y el compartido (lo que haya dejado la última PC que corrió esto) -
    # así, si esta PC llevaba semanas apagada mientras otra seguía
    # validando cargas, se respeta ese avance en vez de reprocesar todo lo
    # que ya se confirmó mientras tanto.
    last_id_compartido = _leer_last_id_compartido(credentials_path, estado_sheet_url)
    if last_id_compartido is not None:
        if last_id_compartido > estado["last_id"]:
            logger.info(f"Last_id compartido ({last_id_compartido}) es mayor que el local "
                        f"({estado['last_id']}); se toma el compartido.")
        estado["last_id"] = max(estado["last_id"], last_id_compartido)

    if estado["last_id"] == 0:
        # Primera ejecución de verdad (ninguna de las PC del equipo ha
        # corrido esto todavía): no reprocesa el historial viejo.
        mensajes, _ = _obtener_mensajes(cfg["bitrix_webhook_url"], chat_id)
        if mensajes:
            estado["last_id"] = max(int(m["id"]) for m in mensajes)
        logger.info(f"Primera ejecución: arrancando desde el mensaje {estado['last_id']}.")

    if not dry_run:
        _guardar_estado(estado)
        _guardar_last_id_compartido(credentials_path, estado_sheet_url, estado["last_id"])

    lookback_n = int(cfg.get("lookback_n", 10) or 10)
    # Confirmados ya conocidos: local (este PC, vía el CSV de auditoría) +
    # compartido (lo que hayan confirmado las otras PC, vía Sheets). Se
    # cargan una sola vez al arrancar y se van completando en memoria a
    # medida que se procesan mensajes -no hace falta releer el CSV entero
    # ni golpear Sheets en cada ciclo, solo cuando hay algo nuevo que
    # avisar-. Ver el comentario largo junto a _cargar_ids_registrados_localmente.
    ids_confirmados_local = _cargar_ids_registrados_localmente()
    ids_confirmados_compartidos = _leer_confirmados_compartidos(credentials_path, estado_sheet_url)
    # ids ya reintentados EN ESTA CORRIDA: red de seguridad final por si un
    # mensaje sigue sin aparecer en ninguno de los dos registros de arriba
    # (ej. el CSV se borró a mano) -así, en el peor caso, se reintenta una
    # sola vez por corrida, nunca en cada poll_interval sin fin-.
    ids_reintentados_esta_corrida = set()

    logger.info(f"Monitoreando {chat_id} cada {poll_interval}s (ventana de reintento: {lookback_n} mensajes).")

    while stop_event is None or not stop_event.is_set():
        try:
            mensajes, _archivos = _obtener_mensajes(cfg["bitrix_webhook_url"], chat_id)
            mensajes.sort(key=lambda m: int(m["id"]))
            nuevos = [m for m in mensajes if int(m["id"]) > estado["last_id"]]

            # Cargas que un compañero ya confirmó a mano en el chat (sin usar
            # el programa) dentro de esta misma tanda de mensajes -ver el
            # comentario junto a _ids_confirmados_manualmente-.
            ids_confirmados_manualmente = _ids_confirmados_manualmente(mensajes)

            # --- Ventana de reintento: hasta `lookback_n` mensajes ANTES de
            # last_id (dentro de la misma tanda ya traída, sin pedir nada
            # extra a Bitrix) que sean "CARGA..." y no consten como
            # confirmados ni local ni compartidamente -por si alguno se
            # coló sin confirmar-. ---
            confirmados_conocidos = ids_confirmados_local | ids_confirmados_compartidos
            anteriores = [m for m in mensajes if int(m["id"]) <= estado["last_id"]]
            anteriores.sort(key=lambda m: int(m["id"]), reverse=True)
            candidatos_atras = [
                m for m in anteriores[:lookback_n]
                if int(m["id"]) not in confirmados_conocidos
                and int(m["id"]) not in ids_reintentados_esta_corrida
                and CARGA_RE.search(m.get("text", "") or "")
            ]

            nuevas_confirmaciones = set()

            for msg in candidatos_atras:
                msg_id = int(msg["id"])
                try:
                    if msg_id in ids_confirmados_manualmente:
                        _registrar_confirmacion_manual(msg, cfg)
                    else:
                        logger.warning(f"Mensaje {msg_id} quedó antes del last_id sin registro de confirmación; "
                                        "se reprocesa (ventana de reintento).")
                        _procesar_mensaje(msg, cfg)
                    nuevas_confirmaciones.add(msg_id)
                except Exception as e:
                    logger.error(f"Error reprocesando mensaje {msg_id} (ventana de reintento): {e}")
                ids_reintentados_esta_corrida.add(msg_id)

            for msg in nuevos:
                msg_id = int(msg["id"])
                try:
                    if msg_id in ids_confirmados_manualmente:
                        _registrar_confirmacion_manual(msg, cfg)
                    else:
                        _procesar_mensaje(msg, cfg)
                    nuevas_confirmaciones.add(msg_id)
                except Exception as e:
                    logger.error(f"Error procesando mensaje {msg_id}: {e}")
                estado["last_id"] = max(estado["last_id"], msg_id)
                if not dry_run:
                    _guardar_estado(estado)

            if nuevas_confirmaciones and not dry_run:
                ids_confirmados_local.update(nuevas_confirmaciones)
                ids_confirmados_compartidos.update(nuevas_confirmaciones)
                _guardar_confirmados_compartidos(credentials_path, estado_sheet_url, ids_confirmados_compartidos)

            if nuevos and not dry_run:
                # Se sincroniza una vez por tanda (no por mensaje) para no
                # saturar la cuota de la API de Sheets.
                _guardar_last_id_compartido(credentials_path, estado_sheet_url, estado["last_id"])

        except Exception as e:
            logger.error(f"Error consultando Bitrix24: {e}")

        time.sleep(poll_interval)


if __name__ == "__main__":
    run()
