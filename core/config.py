"""
Configuración de la aplicación: valores por defecto y lectura/escritura
de scheduler_config.json.
"""
import json
import logging

from .paths import CONFIG_FILE, CREDENTIALS_FILE

logger = logging.getLogger("main")

DEFAULT_CONFIG = {
    "horarios_lun_vie": ["08:10", "11:00", "14:00", "16:00"],
    "horarios_sabado": ["08:10", "11:00"],
    "tolerancia_min": 1.5,
    "sleep_interval": 10,
    "lock_ttl_min": 15,
    "google_sheet_url": "https://docs.google.com/spreadsheets/d/1VeBeuG_sR1HBNJuzfmos99XyEI8-FQos8kVJHUmxq-w/edit?gid=0",
    "credentials_path": str(CREDENTIALS_FILE),
    # Webhook de Bitrix24 compartido por Cargas VOIP, Tickets, Renovación
    # DIDWW y Notificaciones -antes cada uno guardaba el suyo por separado
    # aunque siempre fuera la misma cuenta-. Se configura una sola vez en
    # la pestaña Configuración. Ver get_shared_webhook_url() más abajo para
    # la migración desde las configuraciones viejas por módulo.
    "bitrix_webhook_url": "",
    "enabled_providers": [],
    "providers_config": {},
    "provider_order": [],
    "ui_collapsed_sections": {},
    "cargas_voip_config": {
        "chat_id": "chat135",
        "poll_interval": 20,
        "tolerancia_pct": 0.5,
        "tolerancia_trm_pct": 0.1,
        "tesseract_cmd": "",
        "debug_ocr": True,
        # Hoja de Sheets compartida entre las 3 PC que rotan el uso de este
        # monitor, donde se guarda el last_id -ver cargas_voip.py-.
        "estado_sheet_url": "https://docs.google.com/spreadsheets/d/1ql2y9RBBS4aelGIIawJNielWYo_BUwxtNRCdvVy_gac/edit?gid=0",
        # Cuántos mensajes ANTERIORES a last_id se revisan en cada ciclo por
        # si alguno se quedó sin confirmar (ver "ventana de reintento").
        "lookback_n": 10
    },
    "auto_like_tickets_config": {
        "tickets_chat_id": "chat97",
        "mi_user_id": "13",
        "poll_interval": 20
    },
    "didww_renovacion_config": {
        "chat_id": "",
        "poll_interval": 30,
        "dry_run": True
    },
    "notificaciones_config": {
        "mi_user_id": "13",
        "poll_interval": 15
    }
}


def get_shared_webhook_url(full_cfg: dict) -> str:
    """Webhook de Bitrix24 compartido por los 4 monitores. Si todavía no se
    guardó el compartido (instalaciones viejas, antes de unificarlo), se
    reutiliza el primero que se encuentre entre las configuraciones por
    módulo -compatibilidad hacia atrás, no hace falta que el usuario lo
    vuelva a pegar-."""
    webhook = (full_cfg.get("bitrix_webhook_url") or "").strip()
    if webhook:
        return webhook
    for key in ("cargas_voip_config", "auto_like_tickets_config", "didww_renovacion_config", "notificaciones_config"):
        legacy = (full_cfg.get(key) or {}).get("bitrix_webhook_url", "")
        if legacy:
            return legacy
    return ""


def load_config():
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8-sig') as f:
                cfg = json.load(f)
            for k, v in DEFAULT_CONFIG.items():
                if k not in cfg:
                    cfg[k] = v
            # Migración: si el webhook compartido todavía no está guardado
            # arriba, se completa con el que se encuentre en alguna config
            # vieja por módulo -así no hay que volver a pegarlo-.
            if not cfg.get("bitrix_webhook_url"):
                cfg["bitrix_webhook_url"] = get_shared_webhook_url(cfg)
            # Normalizar listas que deben ser strings
            cfg["provider_order"] = [str(x).strip() for x in cfg.get("provider_order", [])]
            cfg["enabled_providers"] = [str(x).strip() for x in cfg.get("enabled_providers", [])]
            return cfg
        except Exception:
            logger.warning("Error al leer configuración, usando valores por defecto.")
    return DEFAULT_CONFIG.copy()


def save_config(cfg):
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
