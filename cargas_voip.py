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
"""
import os
import re
import csv
import sys
import json
import time
import io
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

CONFIG_FILE = BASE_DIR / "scheduler_config.json"
STATE_FILE = BASE_DIR / "estado_cargas_voip.json"
CSV_FILE = BASE_DIR / "registro_cargas.csv"
LOG_FILE = BASE_DIR / "cargas_voip.log"

TRM_SOAP_URL = "https://www.superfinanciera.gov.co/SuperfinancieraWebServiceTRM/TCRMServicesWebService/TCRMServicesWebService"
# Colombia no usa horario de verano, un offset fijo alcanza.
BOGOTA_TZ = timezone(timedelta(hours=-5))
_trm_oficial_cache = {}

# "CARGA $100.000 TRM 3.126.08" / "CARGA DE $250.000 TRM 3.126.08"
CARGA_RE = re.compile(r"^\s*CARGA\s+(?:DE\s+)?\$?\s*([\d.,]+)\s*TRM\s*([\d.,]+)", re.IGNORECASE)
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
AMOUNT_CANDIDATE_RE = re.compile(r"[\d]{1,3}(?:[.,]\d{3})*[.,]\d{2,3}")

DEFAULT_CARGAS_VOIP_CONFIG = {
    "bitrix_webhook_url": "",
    "chat_id": "chat135",
    "poll_interval": 20,
    "tolerancia_pct": 0.5,
    "tolerancia_trm_pct": 0.1,
    "tesseract_cmd": "",
    "debug_ocr": True,
}

logger = logging.getLogger("cargas_voip")


def _setup_logging(debug: bool):
    logger.handlers.clear()
    handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG if debug else logging.INFO)
    logger.propagate = False


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

    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8-sig") as f:
                full_cfg = json.load(f)
            for k, v in full_cfg.get("cargas_voip_config", {}).items():
                if v not in ("", None):
                    cfg[k] = v
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
OCR_PSM_MODES = (6, 11, 4, 3, 12, 7)


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

        valor = _parsear_usd(candidatos[-1])
        if primer_candidato is None:
            primer_candidato = valor

        if usd_esperado:
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
        _responder(webhook_url, chat_id, f"⚠️ Revisar manual: el mensaje \"{texto}\" no trae imagen adjunta.", reply_id=msg_id)
        _registrar(pesos, trm, None, {"estado": "REVISAR_MANUAL"}, trm_check, msg_id)
        return

    try:
        url_descarga = _obtener_url_descarga(webhook_url, file_ids[0])
    except Exception as e:
        logger.warning(f"Error obteniendo DOWNLOAD_URL: {e}")
        _responder(webhook_url, chat_id, f"⚠️ Revisar manual: no pude obtener el archivo adjunto de \"{texto}\".", reply_id=msg_id)
        _registrar(pesos, trm, None, {"estado": "REVISAR_MANUAL"}, trm_check, msg_id)
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

    _responder(webhook_url, chat_id, "\n".join(lineas), reply_id=msg_id)
    _registrar(pesos, trm, usd_real, resultado, trm_check, msg_id)
    logger.info(f"Mensaje {msg_id} procesado -> {resultado['estado']}")


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

    estado = _cargar_estado()
    chat_id = cfg["chat_id"]
    poll_interval = int(cfg.get("poll_interval", 20))

    if estado["last_id"] == 0:
        # Primera ejecución: no reprocesa el historial viejo.
        mensajes, _ = _obtener_mensajes(cfg["bitrix_webhook_url"], chat_id)
        if mensajes:
            estado["last_id"] = max(int(m["id"]) for m in mensajes)
            _guardar_estado(estado)
        logger.info(f"Primera ejecución: arrancando desde el mensaje {estado['last_id']}.")

    logger.info(f"Monitoreando {chat_id} cada {poll_interval}s.")

    while stop_event is None or not stop_event.is_set():
        try:
            mensajes, _archivos = _obtener_mensajes(cfg["bitrix_webhook_url"], chat_id)
            nuevos = [m for m in mensajes if int(m["id"]) > estado["last_id"]]
            nuevos.sort(key=lambda m: int(m["id"]))

            for msg in nuevos:
                try:
                    _procesar_mensaje(msg, cfg)
                except Exception as e:
                    logger.error(f"Error procesando mensaje {msg.get('id')}: {e}")
                estado["last_id"] = max(estado["last_id"], int(msg["id"]))
                _guardar_estado(estado)

        except Exception as e:
            logger.error(f"Error consultando Bitrix24: {e}")

        time.sleep(poll_interval)


if __name__ == "__main__":
    run()
