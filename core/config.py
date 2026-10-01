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
    "enabled_providers": [],
    "providers_config": {},
    "provider_order": [],
    "cargas_voip_config": {
        "bitrix_webhook_url": "",
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
        "bitrix_webhook_url": "",
        "tickets_chat_id": "chat97",
        "mi_user_id": "13",
        "poll_interval": 20
    },
    "didww_renovacion_config": {
        "bitrix_webhook_url": "",
        "chat_id": "",
        "poll_interval": 30,
        "dry_run": True
    }
}


def load_config():
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8-sig') as f:
                cfg = json.load(f)
            for k, v in DEFAULT_CONFIG.items():
                if k not in cfg:
                    cfg[k] = v
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
