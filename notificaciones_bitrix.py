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

Al hacer clic en la notificación (ver _click_abrir_bitrix): si ya hay
una ventana de navegador mostrando el Messenger de Bitrix24 (o cualquier
página de Bitrix24), se la trae al frente -no se abre una pestaña
nueva-; si no hay ninguna, ahí sí se abre una. Para que Windows pueda
"avisarle" a esta función cuando se hace clic (incluso si el monitor ya
no está corriendo), se usa el mecanismo de protocolo+IPC de winotify
(Registry/Notifier): ver manejar_click_notificacion() y el branch
correspondiente en main.py.

Se ejecuta como subproceso en segundo plano, igual que los otros
monitores de la aplicación.
"""
import os
import re
import sys
import json
import time
import logging
import webbrowser
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import requests
from winotify import Notifier, Registry

try:
    import win32gui
    import win32con
    import win32process
    import win32api
    _PYWIN32_OK = True
except ImportError:
    _PYWIN32_OK = False

if getattr(sys, 'frozen', False):
    BASE_DIR = Path(sys.executable).resolve().parent
else:
    BASE_DIR = Path(__file__).resolve().parent

from core.logging_tools import configurar_logger_rotativo
from core.config import get_shared_webhook_url, get_shared_mi_user_id
from core.paths import MAIN_SCRIPT_PATH
from remote_lock import verificar_bloqueo

CONFIG_FILE = BASE_DIR / "scheduler_config.json"
STATE_FILE = BASE_DIR / "estado_notificaciones.json"
LOG_FILE = BASE_DIR / "notificaciones_bitrix.log"
ICON_PATH = BASE_DIR / "icono.ico"

APP_ID = "Saldos Scheduler - Canales abiertos"

DEFAULT_NOTIFICACIONES_CONFIG = {
    "poll_interval": 15,
}

logger = logging.getLogger("notificaciones_bitrix")

_BBCODE_RE = re.compile(r"\[/?[a-zA-Z0-9=\"'_ ]+\]")


def _setup_logging():
    configurar_logger_rotativo(logger, LOG_FILE, backup_count=3, nivel=logging.DEBUG)


def load_notificaciones_config() -> dict:
    """Igual que en los demás monitores: valores por defecto, variables de
    entorno y lo guardado desde la pestaña "Notificaciones" de la GUI. El
    Webhook de Bitrix24 es el compartido entre todos los monitores -se
    configura una sola vez en la pestaña Configuración-."""
    cfg = DEFAULT_NOTIFICACIONES_CONFIG.copy()
    cfg["bitrix_webhook_url"] = ""
    cfg["mi_user_id"] = "13"

    env_webhook = os.environ.get("BITRIX_WEBHOOK_URL")
    env_user_id = os.environ.get("MI_USER_ID")
    if os.environ.get("POLL_INTERVAL_SECONDS"):
        cfg["poll_interval"] = int(os.environ["POLL_INTERVAL_SECONDS"])

    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8-sig") as f:
                full_cfg = json.load(f)
            for k, v in full_cfg.get("notificaciones_config", {}).items():
                if v not in ("", None):
                    cfg[k] = v
            cfg["bitrix_webhook_url"] = get_shared_webhook_url(full_cfg)
            cfg["mi_user_id"] = get_shared_mi_user_id(full_cfg)
        except Exception:
            pass

    if env_webhook:
        cfg["bitrix_webhook_url"] = env_webhook
    if env_user_id:
        cfg["mi_user_id"] = env_user_id

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
# Enfocar o abrir el Messenger de Bitrix24 (clic en la notificación)
# ---------------------------------------------------------------------------
def _buscar_ventana_bitrix():
    """Busca una ventana de navegador que ya esté mostrando el Messenger
    de Bitrix24 (se identifica por el título de esa página: Bitrix24
    siempre la titula "Mensajería Instantánea", con el contador de no
    leídos delante). Si no hay ninguna mostrando justo esa página, se
    acepta cualquier ventana de Bitrix24 -mejor llevar ahí que abrir una
    pestaña nueva-. Devuelve su hwnd, o None si no encuentra ninguna."""
    if not _PYWIN32_OK:
        return None

    mensajeria = []
    cualquiera = []

    def _cb(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return
        titulo = win32gui.GetWindowText(hwnd)
        if not titulo:
            return
        if "Mensajería Instantánea" in titulo:
            mensajeria.append(hwnd)
        elif "Bitrix24" in titulo:
            cualquiera.append(hwnd)

    win32gui.EnumWindows(_cb, None)
    return (mensajeria or cualquiera or [None])[0]


def _enfocar_ventana(hwnd):
    """Trae una ventana al frente. Un proceso en segundo plano normalmente
    no puede robarle el foco a otra aplicación -Windows lo bloquea a
    propósito-, salvo que se "adjunte" temporalmente a la cola de eventos
    de la ventana en primer plano (AttachThreadInput); es el truco
    estándar para esto."""
    if win32gui.IsIconic(hwnd):
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)

    fg_hwnd = win32gui.GetForegroundWindow()
    current_thread = win32api.GetCurrentThreadId()
    fg_thread = win32process.GetWindowThreadProcessId(fg_hwnd)[0] if fg_hwnd else 0
    attached = False
    try:
        if fg_thread and fg_thread != current_thread:
            win32process.AttachThreadInput(current_thread, fg_thread, True)
            attached = True
        win32gui.SetForegroundWindow(hwnd)
        win32gui.BringWindowToTop(hwnd)
    finally:
        if attached:
            win32process.AttachThreadInput(current_thread, fg_thread, False)


def _click_abrir_bitrix():
    """Callback que Windows invoca al hacer clic en la notificación (ver
    Notifier/Registry más abajo). Sin argumentos -winotify los llama
    así-: vuelve a leer la configuración para no depender de closures."""
    try:
        cfg = load_notificaciones_config()
        url = _portal_messenger_url(cfg.get("bitrix_webhook_url", ""))
        if not url:
            return
        hwnd = _buscar_ventana_bitrix()
        if hwnd:
            _enfocar_ventana(hwnd)
        else:
            webbrowser.open(url)
    except Exception as e:
        logger.error(f"Error al abrir/enfocar Bitrix24 desde la notificación: {e}")


# ---------------------------------------------------------------------------
# Notificación de escritorio
# ---------------------------------------------------------------------------
def _registry_executable_y_script():
    """Comando que Windows debe ejecutar al hacer clic en la notificación
    (se guarda en el Registro, ver winotify.Registry). Hay que citar las
    rutas a mano -winotify no lo hace-: el intérprete de esta misma PC
    vive en una carpeta con espacio en el nombre (...\\WINDOWS 11\\...) y
    sin comillas Windows lo partiría mal."""
    if getattr(sys, 'frozen', False):
        return f'"{sys.executable}"', ''
    else:
        return f'"{sys.executable}"', f'"{MAIN_SCRIPT_PATH}"'


def _crear_notifier():
    executable, script_path = _registry_executable_y_script()
    registry = Registry(app_id=APP_ID, executable=executable, script_path=script_path)
    notifier = Notifier(registry)
    notifier.register_callback(_click_abrir_bitrix)
    return notifier


def manejar_click_notificacion():
    """Punto de entrada cuando Windows reabre la aplicación porque se hizo
    clic en una notificación y el monitor (--notificaciones) ya no estaba
    corriendo -mientras sigue vivo, en cambio, el clic se le avisa
    directamente por IPC sin relanzar nada-. Ver el branch
    correspondiente en main.py."""
    try:
        notifier = _crear_notifier()
        notifier.start()
    except Exception:
        # Puede pasar si quedó un archivo de PID de una sesión anterior
        # que no se cerró bien (ej. el proceso se forzó a terminar desde
        # el Administrador de tareas): winotify cree que el monitor sigue
        # vivo e intenta avisarle por una tubería que ya nadie escucha.
        # En ese caso, se ejecuta la acción directo acá en vez de fallar.
        _click_abrir_bitrix()


def _notificar(notifier, titulo, mensaje):
    try:
        notif = notifier.create_notification(
            title=titulo,
            msg=mensaje,
            icon=str(ICON_PATH) if ICON_PATH.exists() else "",
            duration="long",
            launch=_click_abrir_bitrix,
        )
        notif.show()
    except Exception as e:
        logger.error(f"No se pudo mostrar la notificación: {e}")


# ---------------------------------------------------------------------------
# Un ciclo de sondeo
# ---------------------------------------------------------------------------
def _procesar_canales(cfg, estado, primera_vez, notifier):
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
                    _notificar(notifier, f"Nuevo chat: {titulo}", _texto_preview(msg.get("text")))
            estado[dialog_id] = msg_id
            continue

        if msg_id and msg_id != anterior:
            autor = str(msg.get("author_id", ""))
            if autor != mi_user_id:
                logger.info(f"Mensaje nuevo en {titulo} (mensaje {msg_id}).")
                _notificar(notifier, titulo, _texto_preview(msg.get("text")))
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
    estado = _cargar_estado()
    primera_vez = not estado

    # notifier.start() deja un hilo escuchando en segundo plano: si el
    # usuario hace clic en una notificación mientras este monitor sigue
    # corriendo, el clic llega por ahí (sin relanzar nada); ver
    # manejar_click_notificacion() para cuando el monitor ya no está vivo.
    notifier = _crear_notifier()
    notifier.start()

    logger.info(f"Monitoreando Canales abiertos cada {poll_interval}s (usuario del webhook: {cfg.get('mi_user_id')}).")

    while stop_event is None or not stop_event.is_set():
        try:
            _procesar_canales(cfg, estado, primera_vez, notifier)
            primera_vez = False
        except Exception as e:
            logger.error(f"Error consultando Bitrix24: {e}")

        time.sleep(poll_interval)


if __name__ == "__main__":
    run()
