#!/usr/bin/env python3
"""
Renovación automática de líneas DIDWW — Bitrix24 (polling)
-----------------------------------------------------------
Revisa periódicamente los mensajes nuevos del chat de Bitrix24 configurado
en la pestaña "Renovación DIDWW" de la aplicación (el chat donde el equipo
pide activar / renovar / bloquear líneas, ej. "ACTIVACION LINEAS DIDWW-
SUSPENSION DE LINEAS Y ACTIVACIÓN...").

Solo actúa cuando un mensaje cumple LAS DOS condiciones:
  1. Menciona al menos una línea "(Didww)" / "(DIDWW)" — cualquier otro
     proveedor (Sipmovil, Vivoz, etc.) se ignora por completo, no se toca
     nada. Un mismo mensaje puede listar VARIAS líneas DIDWW (una por
     renglón) y pedir "Renovar" una sola vez al final: se renuevan todas.
  2. Contiene la palabra "Renovar" — cualquier otra acción sobre una línea
     DIDWW (bloquear, activar, etc.) también se ignora; por ahora solo se
     automatiza la renovación.

Cuando se cumplen ambas, extrae el/los número(s) de línea (el que sigue a
"(Didww)" en cada renglón, normalizando los móviles colombianos que llegan
sin el indicativo 57 — ver `_normalizar_numero_co`) y llama a la API de
DIDWW para renovarla. La forma exacta de renovar depende del estado ACTUAL
de la línea (ver `renovar_did`, que replica las acciones "renew"/"restore"
del script de referencia 'didww_renew.py'):
  - Si está "terminated" (suspendida): terminated=False + billing_cycles_count=0
    ("restore").
  - Si ya está activa: billing_cycles_count=1 ("renew" — un ciclo más).

IMPORTANTE (confirmado con soporte de DIDWW, chat del 2026-09-11):
billing_cycles_count solo se aplica EN LA FECHA DE VENCIMIENTO de la línea,
no al momento del PATCH — no existe en la API v3 un equivalente al botón de
pago inmediato "Renew DID(s)" del panel. Por eso "Renovar" solo tiene efecto
inmediato en líneas YA vencidas (el ciclo pendiente se procesa casi al
instante); en una línea que todavía no vence, el PATCH responde 200 OK pero
NO mueve expires_at hoy — recién lo hará solo si sigue con
billing_cycles_count > 0 cuando llegue esa fecha. `renovar_did` compara
expires_at antes/después y `_procesar_mensaje` usa esa comparación (no el
200 OK) para decidir si la línea realmente se renovó.

En modo REAL responde en el mismo chat con la misma palabra que ya usa el
equipo manualmente: "RENOVADA" (una línea) o "RENOVADAS" (varias) SOLO
cuando expires_at realmente avanzó; si la línea no vencía todavía, se
avisa que hay que renovarla a mano desde el panel de DIDWW. En modo de
PRUEBA (dry-run) NUNCA se escribe nada en el chat — el resultado (qué se
habría renovado, qué no se encontró) queda solo en el log.

La API Key de DIDWW NO se guarda por separado para este monitor: se
reutiliza la misma que ya está configurada para el proveedor "DIDWW" en
la pestaña "Proveedores" (botón "Configurar"), para no tener el mismo
secreto duplicado en dos lugares.

Es independiente de los otros monitores (Cargas VOIP, Tickets): no
comparte estado ni lógica con ellos, aunque normalmente comparten el
mismo Webhook de Bitrix24. Se ejecuta como subproceso en segundo plano,
igual que los demás.
"""
import os
import re
import sys
import json
import time
import logging
from pathlib import Path

import requests

if getattr(sys, 'frozen', False):
    BASE_DIR = Path(sys.executable).resolve().parent
else:
    BASE_DIR = Path(__file__).resolve().parent

from core.logging_tools import configurar_logger_rotativo

CONFIG_FILE = BASE_DIR / "scheduler_config.json"
STATE_FILE = BASE_DIR / "estado_didww_renovacion.json"
LOG_FILE = BASE_DIR / "didww_renovacion.log"

DIDWW_API_BASE_URL = "https://api.didww.com/v3"

# Detecta que el mensaje habla de una línea "(Didww)" y captura el número
# que viene justo después (ej. "Linea Did Venezuela (Didww): 582127201213
# + 5 canales" -> captura "582127201213", sin tragarse el "+ 5" de después
# porque el "+" no forma parte de la clase de caracteres del número).
DIDWW_LINEA_RE = re.compile(r"\(\s*didww\s*\)\s*:?\s*([\d][\d\s.\-]{4,20}\d)", re.IGNORECASE)
RENOVAR_RE = re.compile(r"\brenovar\b", re.IGNORECASE)

DEFAULT_DIDWW_RENOVACION_CONFIG = {
    "bitrix_webhook_url": "",
    "chat_id": "",
    "poll_interval": 30,
    "dry_run": True,
}

logger = logging.getLogger("didww_renovacion")


def _setup_logging():
    # Rota a medianoche igual que los demás monitores: cada día arranca
    # vacío y se conservan 3 días de respaldo (se autoeliminan solos), así
    # no se acumula información indefinidamente. (La rotación en sí vive
    # en core/logging_tools.py -ver el docstring de ese módulo sobre por
    # qué no alcanza con el TimedRotatingFileHandler solo.)
    configurar_logger_rotativo(logger, LOG_FILE, backup_count=3, nivel=logging.DEBUG)


def load_didww_renovacion_config() -> dict:
    """Combina valores por defecto con lo guardado desde la pestaña
    "Renovación DIDWW" de la GUI en scheduler_config.json. La API Key de
    DIDWW se toma de providers_config.DIDWW.api_key (la misma que ya usa
    la pestaña "Proveedores" para consultar saldo), no se duplica aquí."""
    cfg = DEFAULT_DIDWW_RENOVACION_CONFIG.copy()
    cfg["didww_api_key"] = os.environ.get("DIDWW_API_KEY", "")

    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8-sig") as f:
                full_cfg = json.load(f)
            for k, v in full_cfg.get("didww_renovacion_config", {}).items():
                if v not in ("", None):
                    cfg[k] = v

            api_key = full_cfg.get("providers_config", {}).get("DIDWW", {}).get("api_key", "")
            if api_key:
                cfg["didww_api_key"] = api_key

            # Si todavía no se configuró un Webhook propio para este
            # monitor, se sugiere el mismo que ya esté guardado para
            # Cargas VOIP o Tickets (normalmente es la misma cuenta de
            # Bitrix24), igual que hace la pestaña "Tickets".
            if not cfg.get("bitrix_webhook_url"):
                cfg["bitrix_webhook_url"] = (
                    full_cfg.get("cargas_voip_config", {}).get("bitrix_webhook_url", "")
                    or full_cfg.get("auto_like_tickets_config", {}).get("bitrix_webhook_url", "")
                )
        except Exception:
            pass

    return cfg


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
# Bitrix24
# ---------------------------------------------------------------------------
MENSAJES_POR_PAGINA = 50
MAX_PAGINAS_HACIA_ATRAS = 10  # tope: no más de 500 mensajes de historial por ronda


def _obtener_pagina_mensajes(webhook_url, chat_id, last_id=None):
    params = {"DIALOG_ID": chat_id, "LIMIT": MENSAJES_POR_PAGINA}
    if last_id:
        params["LAST_ID"] = last_id
    resp = requests.get(f"{webhook_url}im.dialog.messages.get", params=params, timeout=20)
    resp.raise_for_status()
    result = resp.json().get("result", {})
    return result.get("messages", [])


def _obtener_mensajes(webhook_url, chat_id, last_id_objetivo=None):
    """Trae los mensajes recientes del chat. Si se indica `last_id_objetivo`,
    y los últimos MENSAJES_POR_PAGINA no alcanzan a cubrirlo (porque el chat
    tuvo mucho movimiento desde la última ronda), sigue pidiendo "la página
    anterior" (parámetro LAST_ID de Bitrix24) hasta llegar a ese punto, con
    un tope de MAX_PAGINAS_HACIA_ATRAS páginas para no quedarse pidiendo
    historial indefinidamente si el monitor estuvo mucho tiempo apagado."""
    todos = []
    cursor = None
    for _ in range(MAX_PAGINAS_HACIA_ATRAS):
        pagina = _obtener_pagina_mensajes(webhook_url, chat_id, last_id=cursor)
        if not pagina:
            break
        todos.extend(pagina)

        ids_pagina = [int(m["id"]) for m in pagina]
        minimo = min(ids_pagina)

        if last_id_objetivo is None or minimo <= last_id_objetivo:
            break  # ya cubrimos todo lo que hacía falta (o no se pidió ir más atrás)
        if cursor == minimo:
            break  # el chat no tiene más historial que devolver, evita bucle
        cursor = minimo

    return todos


def _responder(webhook_url, chat_id, texto, reply_id=None):
    payload = {"DIALOG_ID": chat_id, "MESSAGE": texto}
    if reply_id is not None:
        payload["REPLY_ID"] = reply_id
    requests.post(f"{webhook_url}im.message.add", json=payload, timeout=15)


# ---------------------------------------------------------------------------
# DIDWW (adaptado de didww_renew.py, que compartió Andres)
# ---------------------------------------------------------------------------
def _didww_headers(api_key):
    return {
        "Accept": "application/vnd.api+json",
        "Content-Type": "application/vnd.api+json",
        "Api-Key": api_key,
    }


def buscar_did_por_numero(api_key, numero):
    """Busca el DID por número (GET, de solo lectura, sin costo)."""
    resp = requests.get(
        f"{DIDWW_API_BASE_URL}/dids",
        headers=_didww_headers(api_key),
        params={"filter[number]": numero},
        timeout=20,
    )
    resp.raise_for_status()
    data = resp.json().get("data", [])
    return data[0] if data else None


def renovar_did(api_key, did, dry_run=False):
    """PATCH /dids/{id}. `did` es el objeto completo devuelto por
    buscar_did_por_numero (trae "id" y "attributes").

    Replica lo que hace didww_renew.py, que distingue dos casos según el
    estado ACTUAL de la línea (por eso hace falta el objeto completo, no
    solo el id):

      - Si la línea está "terminated" (terminada/suspendida): acción
        "restore", manda terminated=False junto con billing_cycles_count=0.
      - Si la línea NO está terminated (ya estaba activa): acción "renew",
        manda billing_cycles_count=1 (un ciclo más).

    IMPORTANTE (confirmado con soporte de DIDWW, chat del 2026-09-11):
    billing_cycles_count NO es un botón de "renovar ya" — es la cantidad de
    ciclos de auto-renovación que le quedan a la línea, y solo se aplica EN
    LA FECHA DE VENCIMIENTO de esa línea (textual de Arnoldas/soporte: "If
    changed to 1 then on the billing cycle another month will be added").
    No existe en la API v3 un endpoint equivalente al botón "Renew DID(s)"
    del panel (pago inmediato, con selección de meses) — Create Order es
    solo para comprar números nuevos, y Update DID solo fija esta política.

    En la práctica esto significa:
      - Si la línea YA venció (expires_at en el pasado): el PATCH sí puede
        verse reflejado casi al instante, porque el ciclo pendiente que
        DIDWW tenía en cola se procesa apenas hay billing_cycles_count > 0
        (caso 576042040696, venció 2026-09-09, se renovó el 2026-09-11).
      - Si la línea AÚN NO vence (expires_at en el futuro): el PATCH no
        mueve expires_at hoy, por más que la respuesta sea 200 OK — el
        cambio de fecha recién ocurre solo cuando llegue esa fecha (caso
        576042040324, vence 2026-09-27, no cambió al probar el 2026-09-11).
        Para esos casos hace falta la acción de pago manual en el panel
        (Renew DID(s)) mientras no haya un endpoint de API para eso.

    Por eso esta función YA NO asume éxito solo porque la respuesta HTTP
    fue 200: compara expires_at antes/después y lo informa en el resultado
    (clave "cambio"), para que quien llama pueda distinguir una renovación
    real de un no-op silencioso — ese fue justamente el bug original: el
    log decía "renovada correctamente" para las dos líneas de un mismo
    mensaje, pero solo una había cambiado de verdad en el panel de DIDWW.
    """
    did_id = did["id"]
    attrs_antes = did.get("attributes") or {}
    terminated_actual = bool(attrs_antes.get("terminated"))
    expires_at_antes = attrs_antes.get("expires_at")

    if terminated_actual:
        attributes = {"terminated": False, "billing_cycles_count": 0}
    else:
        attributes = {"billing_cycles_count": 1}

    body = {
        "data": {
            "id": did_id,
            "type": "dids",
            "attributes": attributes,
        }
    }
    if dry_run:
        logger.info(f"[DRY RUN] Se habría enviado PATCH {DIDWW_API_BASE_URL}/dids/{did_id} "
                    f"(terminated actual={terminated_actual}, expires_at actual={expires_at_antes}): "
                    f"{json.dumps(body)}")
        return {"dry_run": True, "cambio": None, "expires_at_antes": expires_at_antes, "expires_at_despues": None}

    resp = requests.patch(
        f"{DIDWW_API_BASE_URL}/dids/{did_id}",
        headers=_didww_headers(api_key),
        json=body,
        timeout=20,
    )
    resp.raise_for_status()
    resultado = resp.json()
    # Se deja registrada la respuesta completa de DIDWW (no solo si dio
    # 200), y en particular la comparación de expires_at antes/después,
    # para poder diagnosticar sin depender de Postman si algún caso nuevo
    # tampoco refleja el cambio esperado en el panel. Nunca debe
    # interrumpir la renovación si el registro en sí falla.
    expires_at_despues = expires_at_antes
    try:
        expires_at_despues = (resultado.get("data", {}).get("attributes", {}) or {}).get("expires_at")
        logger.debug(f"Respuesta DIDWW para PATCH /dids/{did_id}: {json.dumps(resultado, ensure_ascii=False)}")
        logger.info(f"expires_at de {did_id}: antes={expires_at_antes} -> después={expires_at_despues}")
    except Exception:
        logger.debug(f"Respuesta DIDWW para PATCH /dids/{did_id}: {resultado!r}")

    cambio = expires_at_despues is not None and expires_at_despues != expires_at_antes
    return {
        "dry_run": False,
        "cambio": cambio,
        "expires_at_antes": expires_at_antes,
        "expires_at_despues": expires_at_despues,
        "raw": resultado,
    }


# ---------------------------------------------------------------------------
# Parseo de mensajes
# ---------------------------------------------------------------------------
def _normalizar_numero_co(numero: str) -> str:
    """Las líneas MÓVILES de Colombia a veces se piden sin el indicativo de
    país 57 (ej. "3009121940": 10 dígitos, empieza en "3" — así arrancan
    todos los celulares colombianos), pero en DIDWW están guardadas CON el
    indicativo, así que la búsqueda falla si no se lo agregamos. Si detecta
    ese patrón exacto (10 dígitos, empieza en "3"), antepone "57". Si el
    número ya lo trae (12 dígitos, ej. "573009121940") no se toca -> no se
    duplica el indicativo. Las líneas fijas (ej. "576014898313", que ya
    llegan con 57 + indicativo de ciudad) tampoco se tocan, porque no
    cumplen el patrón de 10 dígitos empezando en 3."""
    if len(numero) == 10 and numero.startswith("3"):
        return "57" + numero
    return numero


def extraer_numeros_didww(texto: str):
    """Un mensaje de renovación puede listar VARIAS líneas "(Didww)" a la
    vez (una por renglón) y pedir "Renovar" una sola vez al final, ej.:

        Linea did Movil (Didww): 3009121940
        Linea did Fija Bogota (Didww): 576014898313
        Linea did Movil (Didww): 3009121952

        Renovar

    Por eso se buscan TODAS las coincidencias (no solo la primera), se
    limpian de espacios/puntos/guiones y se normalizan (ver
    `_normalizar_numero_co`), en el mismo orden en que aparecen. Si el
    mensaje no menciona ninguna línea (Didww), devuelve una lista vacía ->
    se ignora por completo (otros proveedores no deben disparar nada)."""
    numeros = [re.sub(r"\D", "", m) for m in DIDWW_LINEA_RE.findall(texto or "")]
    return [_normalizar_numero_co(n) for n in numeros]


def es_solicitud_de_renovacion(texto: str) -> bool:
    return bool(RENOVAR_RE.search(texto or ""))


# ---------------------------------------------------------------------------
# Procesar un mensaje nuevo
# ---------------------------------------------------------------------------
def _procesar_mensaje(msg, cfg):
    params = msg.get("params")
    if isinstance(params, dict) and params.get("IS_DELETED") == "Y":
        logger.debug(f"[mensaje {msg.get('id')}] eliminado, se ignora.")
        return

    texto = msg.get("text", "") or ""
    msg_id = msg.get("id")

    numeros = extraer_numeros_didww(texto)
    if not numeros:
        logger.debug(f"[mensaje {msg_id}] no menciona ninguna línea (Didww), se ignora.")
        return  # no es una línea DIDWW -> no se toca nada (otros proveedores, etc.)

    if not es_solicitud_de_renovacion(texto):
        logger.debug(f"[mensaje {msg_id}] menciona (Didww) pero no dice 'Renovar', se ignora: {texto!r}")
        return  # línea DIDWW pero otra acción (bloquear, activar, ...) -> no se automatiza

    logger.info(f"[mensaje {msg_id}] solicitud de renovación DIDWW detectada, números={numeros}.")

    webhook_url = cfg.get("bitrix_webhook_url", "")
    chat_id = cfg.get("chat_id", "")
    dry_run = bool(cfg.get("dry_run", True))

    if not cfg.get("didww_api_key"):
        logger.error("No hay API Key de DIDWW configurada (pestaña Proveedores -> DIDWW -> Configurar).")
        # En modo de prueba NUNCA se escribe en el chat, ni siquiera avisos
        # como este -> solo queda en el log, para no generar ruido mientras
        # se está calibrando la detección.
        if not dry_run:
            _responder(webhook_url, chat_id,
                       f"⚠️ No pude renovar la(s) línea(s) {', '.join(numeros)}: falta configurar la API Key de "
                       "DIDWW (Proveedores -> DIDWW -> Configurar).",
                       reply_id=msg_id)
        return

    # Un solo mensaje puede pedir renovar varias líneas a la vez -> se
    # procesa cada número por separado (cada uno con su propio DID id).
    lineas_ok = []
    lineas_fallo = []
    for numero in numeros:
        try:
            did = buscar_did_por_numero(cfg["didww_api_key"], numero)
            if not did:
                logger.warning(f"No se encontró ningún DID con el número {numero}.")
                lineas_fallo.append(f"{numero}: no encontré ninguna línea DIDWW con ese número.")
                continue

            resultado = renovar_did(cfg["didww_api_key"], did, dry_run=dry_run)

            if dry_run:
                logger.info(f"[DRY RUN] Línea {numero} (id={did['id']}) -> se habría renovado.")
                lineas_ok.append(numero)
            elif resultado["cambio"]:
                logger.info(
                    f"Línea {numero} (id={did['id']}) renovada correctamente "
                    f"({resultado['expires_at_antes']} -> {resultado['expires_at_despues']})."
                )
                lineas_ok.append(numero)
            else:
                # DIDWW respondió 200 OK pero expires_at no cambió. Confirmado con
                # soporte de DIDWW (2026-09-11): billing_cycles_count solo se aplica
                # en la fecha de vencimiento de la línea, no al momento del PATCH.
                # Si la línea todavía no llega a esa fecha, no hay forma de
                # adelantarle la renovación vía API — hace falta el botón de pago
                # manual "Renew DID(s)" en el panel de DIDWW.
                logger.warning(
                    f"Línea {numero} (id={did['id']}) NO se renovó: DIDWW respondió 200 OK pero "
                    f"expires_at no cambió (sigue en {resultado['expires_at_antes']}). Probablemente la línea "
                    "aún no llega a su fecha de corte -> hay que renovarla manualmente desde el panel de "
                    "DIDWW (Renew DID(s), tiene costo)."
                )
                lineas_fallo.append(
                    f"{numero}: DIDWW no extendió la fecha (expires_at sigue en {resultado['expires_at_antes']}), "
                    "probablemente porque la línea aún no llega a su fecha de corte. Debe renovarse manualmente "
                    "desde el panel de DIDWW."
                )

        except Exception as e:
            logger.error(f"Error renovando la línea {numero}: {e}")
            lineas_fallo.append(f"{numero}: error al renovar ({e}).")

    if dry_run:
        # Modo de prueba: el resultado (qué se habría renovado, qué no se
        # encontró) queda registrado SOLO en el log — nunca se escribe nada
        # en el chat de Bitrix24. Responder "RENOVADA"/"RENOVADAS" es
        # exclusivo del modo real, más abajo.
        logger.info(f"[DRY RUN] Resumen mensaje {msg_id}: renovaría {lineas_ok or 'ninguna'}; "
                    f"fallos: {lineas_fallo or 'ninguno'}. No se envía nada al chat (modo de prueba).")
        return

    if not lineas_ok:
        _responder(webhook_url, chat_id,
                   "⚠️ No pude renovar ninguna línea:\n" + "\n".join(lineas_fallo), reply_id=msg_id)
        return

    # Modo real: se responde igual que lo hace el equipo manualmente en
    # este chat (una sola palabra: "RENOVADA" para una línea, "RENOVADAS"
    # para varias). Si alguna línea puntual falló, se agrega debajo, para
    # no reportar como resuelto algo que no se pudo renovar.
    texto_respuesta = "RENOVADA" if len(lineas_ok) == 1 else "RENOVADAS"
    if lineas_fallo:
        texto_respuesta += "\n⚠️ " + "\n⚠️ ".join(lineas_fallo)

    _responder(webhook_url, chat_id, texto_respuesta, reply_id=msg_id)


# ---------------------------------------------------------------------------
# Loop principal
# ---------------------------------------------------------------------------
def run(cfg: dict = None, stop_event=None):
    """Punto de entrada del monitor. Se lanza como subproceso en segundo
    plano desde la pestaña "Renovación DIDWW" de la GUI, igual que los
    otros monitores. `stop_event` (threading.Event) es opcional, solo se
    usa si en algún momento se embebe en un hilo en vez de un subproceso
    aparte."""
    cfg = cfg or load_didww_renovacion_config()
    _setup_logging()

    if not cfg.get("bitrix_webhook_url"):
        logger.error("bitrix_webhook_url no configurado.")
        print("ERROR: falta configurar la URL del webhook de Bitrix24 (pestaña 'Renovación DIDWW').", flush=True)
        return
    if not cfg.get("chat_id"):
        logger.error("chat_id no configurado.")
        print("ERROR: falta configurar el Chat ID (pestaña 'Renovación DIDWW').", flush=True)
        return

    if cfg.get("dry_run", True):
        logger.info("Modo de prueba (dry-run) ACTIVADO: no se modificará nada en DIDWW, solo se registra qué haría.")

    estado = _cargar_estado()
    chat_id = cfg["chat_id"]
    poll_interval = int(cfg.get("poll_interval", 30))

    if estado["last_id"] == 0:
        # Primera ejecución: no reprocesa el historial viejo del chat.
        mensajes = _obtener_mensajes(cfg["bitrix_webhook_url"], chat_id)
        if mensajes:
            estado["last_id"] = max(int(m["id"]) for m in mensajes)
            _guardar_estado(estado)
        logger.info(f"Primera ejecución: arrancando desde el mensaje {estado['last_id']}.")

    logger.info(f"Monitoreando {chat_id} cada {poll_interval}s, buscando solicitudes de renovación DIDWW.")

    while stop_event is None or not stop_event.is_set():
        try:
            mensajes = _obtener_mensajes(cfg["bitrix_webhook_url"], chat_id, last_id_objetivo=estado["last_id"])
            vistos = {}
            for m in mensajes:
                vistos[int(m["id"])] = m  # las páginas se pueden solapar en el borde
            nuevos = [m for mid, m in vistos.items() if mid > estado["last_id"]]
            nuevos.sort(key=lambda m: int(m["id"]))

            if len(nuevos) > MENSAJES_POR_PAGINA:
                logger.info(f"Poniéndose al día: {len(nuevos)} mensajes pendientes desde el id {estado['last_id']}.")

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