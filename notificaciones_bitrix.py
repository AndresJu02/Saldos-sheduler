#!/usr/bin/env python3
"""
Notificaciones de escritorio para Canales Abiertos — Bitrix24 (polling)
------------------------------------------------------------------------
Revisa periódicamente, vía `im.recent.get`, los chats de "Canales
abiertos" (Open Lines) del portal de Bitrix24 configurado y dispara una
notificación nativa de Windows (bandeja de notificaciones, como las de
WhatsApp) cuando:

  - Aparece un chat de Canales abiertos que no existía en el sondeo
    anterior (cliente nuevo).
  - Llega un mensaje nuevo en un chat de Canales abiertos ya conocido.

Un chat se identifica como de "Canales abiertos" cuando
`chat.entity_type == "LINES"` en la respuesta de `im.recent.get` -es el
mismo campo que distingue esos chats de los demás chats/grupos internos
del portal-.

No se notifican los mensajes enviados por el propio usuario del Webhook
(`mi_user_id`): igual que WhatsApp, uno no recibe notificación de lo que
uno mismo escribe, el chat no se "cierra" justo porque el agente ya
respondió. En la primera ejecución no se notifica nada del historial ya
existente, solo se toma una foto inicial; a partir de ahí, todo lo nuevo
sí se notifica.

Se ejecuta como subproceso en segundo plano, igual que los otros
monitores de la aplicación.
"""
import os
import re
import sys
import json
import time
import logging
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import requests
from winotify import Notification

if getattr(sys, 'frozen', False):
    BASE_DIR = Path(sys.executable).resolve().parent
else:
    BASE_DIR = Path(__file__).resolve().parent

from core.logging_tools import configurar_logger_rotativo
from remote_lock import verificar_bloqueo

CONFIG_FILE = BASE_DIR / "scheduler_config.json"
STATE_FILE = BASE_DIR / "estado_notificaciones.json"
LOG_FILE = BASE_DIR / "notificaciones_bitrix.log"
ICON_PATH = BASE_DIR / "icono.ico"

APP_ID = "Saldos Scheduler - Canales abiertos"

DEFAULT_NOTIFICACIONES_CONFIG = {
    "bitrix_webhook_url": "",
    "mi_user_id": "13",
    "poll_interval": 15,
}

logger = logging.getLogger("notificaciones_bitrix")

_BBCODE_RE = re.compile(r"\[/?[a-zA-Z0-9=\"'_ ]+\]")


def _setup_logging():
    configurar_logger_rotativo(logger, LOG_FILE, backup_count=3, nivel=logging.DEBUG)


def load_notificaciones_config() -> dict:
    """Igual que en los demás monitores: valores por defecto, variables de
    entorno y lo guardado desde la pestaña "Notificaciones" de la GUI. Si
    no se configuró un Webhook propio, se reutiliza el de Cargas VOIP por
    comodidad (normalmente comparten el mismo)."""
    cfg = DEFAULT_NOTIFICACIONES_CONFIG.copy()

    if os.environ.get("BITRIX_WEBHOOK_URL"):
        cfg["bitrix_webhook_url"] = os.environ["BITRIX_WEBHOOK_URL"]
    if os.environ.get("MI_USER_ID"):
        cfg["mi_user_id"] = os.environ["MI_USER_ID"]
    if os.environ.get("POLL_INTERVAL_SECONDS"):
        cfg["poll_interval"] = int(os.environ["POLL_INTERVAL_SECONDS"])

    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8-sig") as f:
                full_cfg = json.load(f)
            if not cfg["bitrix_webhook_url"]:
                cfg["bitrix_webhook_url"] = full_cfg.get("cargas_voip_config", {}).get("bitrix_webhook_url", "")
            for k, v in full_cfg.get("notificaciones_config", {}).items():
                if v not in ("", None):
                    cfg[k] = v
        except Exception:
            pass

    return cfg


# ---------------------------------------------------------------------------
# Estado local (último mensaje visto por chat, para no repetir avisos)
# ---------------------------------------------------------------------------
def _cargar_estado():
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _guardar_estado(estado):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(estado, f)


# ---------------------------------------------------------------------------
# Bitrix24
# ---------------------------------------------------------------------------
def _obtener_canales_abiertos(webhook_url):
    """Devuelve solo los chats de Canales abiertos (chat.entity_type ==
    'LINES') de la lista de chats recientes del usuario del Webhook."""
    resp = requests.get(
        f"{webhook_url}im.recent.get",
        params={"SKIP_OPENLINES": "N"},
        timeout=20,
    )
    resp.raise_for_status()
    items = resp.json().get("result") or []
    if isinstance(items, dict):
        items = list(items.values())
    return [it for it in items if (it.get("chat") or {}).get("entity_type") == "LINES"]


def _portal_messenger_url(webhook_url):
    partes = urlsplit(webhook_url)
    return urlunsplit((partes.scheme, partes.netloc, "/online/", "", ""))


def _texto_preview(texto, longitud=180):
    texto = _BBCODE_RE.sub("", texto or "").strip()
    texto = re.sub(r"\s+", " ", texto)
    if len(texto) > longitud:
        texto = texto[:longitud - 1].rstrip() + "…"
    return texto or "(sin texto)"


# ---------------------------------------------------------------------------
# Notificación de escritorio
# ---------------------------------------------------------------------------
def _notificar(titulo, mensaje, launch_url):
    try:
        n = Notification(
            app_id=APP_ID,
            title=titulo,
            msg=mensaje,
            icon=str(ICON_PATH) if ICON_PATH.exists() else "",
            duration="long",
            launch=launch_url,
        )
        n.show()
    except Exception as e:
        logger.error(f"No se pudo mostrar la notificación: {e}")


# ---------------------------------------------------------------------------
# Un ciclo de sondeo
# ---------------------------------------------------------------------------
def _procesar_canales(cfg, estado, primera_vez, launch_url):
    canales = _obtener_canales_abiertos(cfg["bitrix_webhook_url"])
    mi_user_id = str(cfg.get("mi_user_id", ""))

    for canal in canales:
        dialog_id = canal.get("id")
        if not dialog_id:
            continue

        msg = canal.get("message") or {}
        msg_id = msg.get("id")
        titulo = canal.get("title") or dialog_id

        anterior = estado.get(dialog_id)

        if primera_vez or anterior is None:
            # Chat visto por primera vez: solo se toma como punto de
            # partida (si ya existía) -no se notifica el historial-,
            # salvo que ya estuviéramos sondeando (ahí sí es un chat
            # nuevo que acaba de aparecer).
            if not primera_vez:
                autor = str(msg.get("author_id", ""))
                if msg_id and autor != mi_user_id:
                    logger.info(f"Chat nuevo en Canales abiertos: {titulo}")
                    _notificar(f"Nuevo chat: {titulo}", _texto_preview(msg.get("text")), launch_url)
            estado[dialog_id] = msg_id
            continue

        if msg_id and msg_id != anterior:
            autor = str(msg.get("author_id", ""))
            if autor != mi_user_id:
                logger.info(f"Mensaje nuevo en {titulo} (mensaje {msg_id}).")
                _notificar(titulo, _texto_preview(msg.get("text")), launch_url)
            estado[dialog_id] = msg_id

    _guardar_estado(estado)


# ---------------------------------------------------------------------------
# Loop principal
# ---------------------------------------------------------------------------
def run(cfg: dict = None, stop_event=None):
    """Punto de entrada del monitor. Se lanza como subproceso en segundo
    plano desde la pestaña "Notificaciones" de la GUI, igual que los
    otros monitores. `stop_event` (threading.Event) es opcional, solo se
    usa si en algún momento se embebe en un hilo en vez de un subproceso
    aparte."""
    if verificar_bloqueo():
        print("\n⚠️  APLICACIÓN BLOQUEADA POR EL ADMINISTRADOR.", flush=True)
        print("   Contacte al soporte para más información.\n", flush=True)
        return

    cfg = cfg or load_notificaciones_config()
    _setup_logging()

    if not cfg.get("bitrix_webhook_url"):
        logger.error("bitrix_webhook_url no configurado.")
        print("ERROR: falta configurar la URL del webhook de Bitrix24 (pestaña 'Notificaciones').", flush=True)
        return

    poll_interval = int(cfg.get("poll_interval", 15))
    launch_url = _portal_messenger_url(cfg["bitrix_webhook_url"])
    estado = _cargar_estado()
    primera_vez = not estado

    logger.info(f"Monitoreando Canales abiertos cada {poll_interval}s (usuario del webhook: {cfg.get('mi_user_id')}).")

    while stop_event is None or not stop_event.is_set():
        try:
            _procesar_canales(cfg, estado, primera_vez, launch_url)
            primera_vez = False
        except Exception as e:
            logger.error(f"Error consultando Bitrix24: {e}")

        time.sleep(poll_interval)


if __name__ == "__main__":
    run()
