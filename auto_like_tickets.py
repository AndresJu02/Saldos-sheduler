#!/usr/bin/env python3
"""
Auto-like de tickets asignados — Bitrix24 (polling)
-----------------------------------------------------
Revisa periódicamente los mensajes nuevos del chat de tickets configurado
en la pestaña "Tickets" de la aplicación. Cuando aparece un mensaje con
el formato exacto:

    "ticket <número> asignado a [USER=<id>]<Nombre>[/USER]"

(sin importar mayúsculas/minúsculas) y ese <id> coincide con el usuario
configurado, le da like automáticamente al mensaje.

Solo reacciona a ese patrón específico de asignación — no a una mención
suelta en cualquier otro mensaje, ni a ningún otro tipo de texto del chat.

Es independiente del monitor de Cargas VOIP: no comparte estado ni
lógica con él, aunque normalmente sí comparten el mismo Webhook de
Bitrix24. Se ejecuta como subproceso en segundo plano, igual que los
otros monitores de la aplicación.
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
STATE_FILE = BASE_DIR / "estado_tickets.json"
LOG_FILE = BASE_DIR / "auto_like_tickets.log"

# "ticket 12071 asignado a [USER=13]Andres Espinosa[/USER]"
TICKET_ASIGNADO_RE = re.compile(r"ticket\s+\d+\s+asignado\s+a\s+\[USER=(\d+)\]", re.IGNORECASE)

DEFAULT_AUTO_LIKE_CONFIG = {
    "bitrix_webhook_url": "",
    "tickets_chat_id": "chat97",
    "mi_user_id": "13",
    "poll_interval": 20,
}

logger = logging.getLogger("auto_like_tickets")


def _setup_logging():
    # Rota el log a la medianoche: cada día arranca vacío y solo se
    # conservan los últimos días (backupCount) en archivos aparte
    # ('auto_like_tickets.log.YYYY-MM-DD'), que se van borrando solos -
    # así no se acumula información indefinidamente. (La rotación en sí
    # vive en core/logging_tools.py -ver el docstring de ese módulo.)
    configurar_logger_rotativo(logger, LOG_FILE, backup_count=3, nivel=logging.DEBUG)


def load_auto_like_config() -> dict:
    """Combina, en orden de prioridad creciente: valores por defecto,
    variables de entorno (heredadas del script original / primer uso) y
    lo guardado desde la pestaña "Tickets" de la GUI en
    scheduler_config.json, que es lo que manda una vez el usuario guarda
    su configuración."""
    cfg = DEFAULT_AUTO_LIKE_CONFIG.copy()

    if os.environ.get("BITRIX_WEBHOOK_URL"):
        cfg["bitrix_webhook_url"] = os.environ["BITRIX_WEBHOOK_URL"]
    if os.environ.get("TICKETS_CHAT_ID"):
        cfg["tickets_chat_id"] = os.environ["TICKETS_CHAT_ID"]
    if os.environ.get("MI_USER_ID"):
        cfg["mi_user_id"] = os.environ["MI_USER_ID"]
    if os.environ.get("POLL_INTERVAL_SECONDS"):
        cfg["poll_interval"] = int(os.environ["POLL_INTERVAL_SECONDS"])

    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8-sig") as f:
                full_cfg = json.load(f)
            for k, v in full_cfg.get("auto_like_tickets_config", {}).items():
                if v not in ("", None):
                    cfg[k] = v
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
    return result.get("messages", [])


def _dar_like(webhook_url, message_id):
    resp = requests.post(
        f"{webhook_url}im.message.like",
        json={"MESSAGE_ID": message_id},
        timeout=15,
    )
    logger.debug(f"Like a mensaje {message_id}: {resp.text[:200]}")


# ---------------------------------------------------------------------------
# Procesar un mensaje nuevo
# ---------------------------------------------------------------------------
def _procesar_mensaje(msg, cfg):
    params = msg.get("params")
    if isinstance(params, dict) and params.get("IS_DELETED") == "Y":
        logger.debug(f"[mensaje {msg.get('id')}] eliminado, se ignora.")
        return

    texto = msg.get("text", "") or ""
    m = TICKET_ASIGNADO_RE.search(texto)

    logger.debug(f"[mensaje {msg.get('id')}] texto={texto!r} -> "
                 f"{'asignado a USER=' + m.group(1) if m else 'no es una asignación de ticket'}")

    if not m:
        return  # no es "ticket X asignado a...", se ignora

    if m.group(1) == str(cfg["mi_user_id"]):
        _dar_like(cfg["bitrix_webhook_url"], msg.get("id"))
        logger.info(f"Like dado al mensaje {msg.get('id')} (ticket asignado).")


# ---------------------------------------------------------------------------
# Loop principal
# ---------------------------------------------------------------------------
def run(cfg: dict = None, stop_event=None):
    """Punto de entrada del monitor. Se lanza como subproceso en segundo
    plano desde la pestaña "Tickets" de la GUI. `stop_event` (threading.Event)
    es opcional, solo se usa si en algún momento se embebe en un hilo en
    vez de un subproceso aparte."""
    cfg = cfg or load_auto_like_config()
    _setup_logging()

    if not cfg.get("bitrix_webhook_url"):
        logger.error("bitrix_webhook_url no configurado.")
        print("ERROR: falta configurar la URL del webhook de Bitrix24 (pestaña 'Tickets').", flush=True)
        return

    estado = _cargar_estado()
    chat_id = cfg["tickets_chat_id"]
    mi_user_id = cfg["mi_user_id"]
    poll_interval = int(cfg.get("poll_interval", 20))

    if estado["last_id"] == 0:
        # Primera ejecución: no le da like a todo el historial viejo.
        mensajes = _obtener_mensajes(cfg["bitrix_webhook_url"], chat_id)
        if mensajes:
            estado["last_id"] = max(int(m["id"]) for m in mensajes)
            _guardar_estado(estado)
        logger.info(f"Primera ejecución: arrancando desde el mensaje {estado['last_id']}.")

    logger.info(f"Monitoreando {chat_id}, dando like a tickets asignados a {mi_user_id}, cada {poll_interval}s.")

    while stop_event is None or not stop_event.is_set():
        try:
            mensajes = _obtener_mensajes(cfg["bitrix_webhook_url"], chat_id)
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
