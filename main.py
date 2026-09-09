#!/usr/bin/env python3
"""
Aplicación unificada de saldos.
- Sin argumentos → interfaz gráfica.
- --scheduler     → inicia el planificador en segundo plano.
- --balance       → ejecuta una ronda de consulta de saldos (con consola propia).
"""
from remote_lock import verificar_bloqueo
import os
import re
import sys
import json
import importlib
import importlib.util
import subprocess
import threading
import time
import uuid
import socket
import zipfile
import shutil
import traceback
import ctypes
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional, Dict

# ---------------------------------------------------------------------------
# Consola dinámica para la ejecución de saldos
# ---------------------------------------------------------------------------
def setup_console():
    """Adjunta o crea una consola y redirige entrada/salida."""
    if not getattr(sys, 'frozen', False):
        return
    kernel32 = ctypes.windll.kernel32
    if kernel32.AttachConsole(-1) == 0:
        kernel32.AllocConsole()
    sys.stdout = open('CONOUT$', 'w')
    sys.stderr = open('CONOUT$', 'w')
    sys.stdin = open('CONIN$', 'r')

# ---------------------------------------------------------------------------
# Rutas base (portable)
# ---------------------------------------------------------------------------
if getattr(sys, 'frozen', False):
    BASE_DIR = Path(sys.executable).resolve().parent
    INTERNAL_DIR = Path(sys._MEIPASS)
else:
    BASE_DIR = Path(__file__).resolve().parent
    INTERNAL_DIR = BASE_DIR

CONFIG_FILE = BASE_DIR / 'scheduler_config.json'
LOG_FILE = BASE_DIR / 'scheduler.log'
CREDENTIALS_FILE = BASE_DIR / 'credenciales.json'

CHROME_DIR = BASE_DIR / 'chrome-portable'
CHROMEDRIVER_DIR = BASE_DIR / 'chromedriver-portable'

DEFAULT_BALANCE_REGEX = r"[-+]?\d[\d\.,]*"

# Versión "de respaldo" usada únicamente si la consulta a Chrome for Testing
# fallara (sin internet, endpoint caído, etc.) y no hubiera nada instalado aún.
CHROME_VERSION_FALLBACK = "148.0.7778.217"
CHROME_URL_FALLBACK = f"https://storage.googleapis.com/chrome-for-testing-public/{CHROME_VERSION_FALLBACK}/win64/chrome-win64.zip"
CHROMEDRIVER_URL_FALLBACK = f"https://storage.googleapis.com/chrome-for-testing-public/{CHROME_VERSION_FALLBACK}/win64/chromedriver-win64.zip"

# Endpoint oficial de Google con la última versión estable "conocida como buena"
# (Known Good Versions) de Chrome y ChromeDriver, siempre sincronizadas entre sí.
CHROME_FOR_TESTING_JSON = "https://googlechromelabs.github.io/chrome-for-testing/last-known-good-versions-with-downloads.json"

# Caché en memoria (por proceso) de la última info consultada, para no golpear
# el endpoint dos veces seguidas cuando se instalan Chrome y ChromeDriver juntos.
_latest_stable_cache = None

# ---------------------------------------------------------------------------
# Configuración por defecto
# ---------------------------------------------------------------------------
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
        "debug_ocr": True
    },
    "auto_like_tickets_config": {
        "bitrix_webhook_url": "",
        "tickets_chat_id": "chat97",
        "mi_user_id": "13",
        "poll_interval": 20
    }
}

# ---------------------------------------------------------------------------
# Logging (solo archivo)
# ---------------------------------------------------------------------------
import logging
logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger("main")

# ---------------------------------------------------------------------------
# Descarga segura
# ---------------------------------------------------------------------------
def download_file(url, dest):
    import requests
    try:
        if sys.stdout is not None:
            from tqdm import tqdm
            logger.info(f"Descargando {url}")
            resp = requests.get(url, stream=True, timeout=120)
            resp.raise_for_status()
            total = int(resp.headers.get('content-length', 0))
            with open(dest, 'wb') as f:
                with tqdm(total=total, unit='B', unit_scale=True, desc=Path(dest).name, file=sys.stdout) as bar:
                    for chunk in resp.iter_content(chunk_size=8192):
                        f.write(chunk)
                        bar.update(len(chunk))
            return
    except Exception:
        pass

    logger.info(f"Descargando {url} (sin barra de progreso)...")
    resp = requests.get(url, stream=True, timeout=120)
    resp.raise_for_status()
    with open(dest, 'wb') as f:
        for chunk in resp.iter_content(chunk_size=8192):
            f.write(chunk)

def get_latest_stable_info(use_cache=True):
    """
    Consulta el endpoint oficial de Chrome for Testing y devuelve
    (version, chrome_url_win64, chromedriver_url_win64) del canal Stable.
    Lanza una excepción si no se puede consultar (sin internet, etc.).
    """
    global _latest_stable_cache
    if use_cache and _latest_stable_cache is not None:
        return _latest_stable_cache

    import requests
    resp = requests.get(CHROME_FOR_TESTING_JSON, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    stable = data["channels"]["Stable"]
    version = stable["version"]
    chrome_url = next(d["url"] for d in stable["downloads"]["chrome"] if d["platform"] == "win64")
    chromedriver_url = next(d["url"] for d in stable["downloads"]["chromedriver"] if d["platform"] == "win64")

    _latest_stable_cache = (version, chrome_url, chromedriver_url)
    return _latest_stable_cache


def _read_version_marker(dir_path: Path):
    marker = dir_path / 'VERSION.txt'
    if marker.exists():
        try:
            return marker.read_text(encoding='utf-8').strip()
        except Exception:
            return None
    return None


def _write_version_marker(dir_path: Path, version: str):
    try:
        (dir_path / 'VERSION.txt').write_text(version, encoding='utf-8')
    except Exception:
        pass


def get_installed_versions():
    """Devuelve (version_chrome, version_chromedriver) instaladas actualmente, o None si no hay."""
    return _read_version_marker(CHROME_DIR), _read_version_marker(CHROMEDRIVER_DIR)


def _robust_rmtree(path: Path, retries=6, delay=1.0):
    """
    Borra un directorio reintentando si algún archivo está bloqueado
    (chrome.dll en uso, antivirus escaneando, etc.). Antes del primer
    intento mata procesos huérfanos de chrome/chromedriver que puedan
    tener el archivo abierto.
    """
    if not path.exists():
        return
    cleanup_orphans()
    last_err = None
    for attempt in range(retries):
        try:
            shutil.rmtree(path)
            return
        except PermissionError as e:
            last_err = e
            if attempt == 0:
                cleanup_orphans()
            time.sleep(delay)
        except FileNotFoundError:
            return
    raise PermissionError(
        f"No se pudo borrar '{path}' tras {retries} intentos "
        f"(probablemente un proceso de Chrome sigue en ejecución): {last_err}"
    )


def ensure_chrome(force=False):
    chrome_exe = CHROME_DIR / 'chrome-win64' / 'chrome.exe'
    if chrome_exe.exists() and not force:
        return str(chrome_exe)

    logger.info("Instalando Chrome portable...")
    try:
        version, chrome_url, _ = get_latest_stable_info()
    except Exception as e:
        logger.warning(f"No se pudo consultar la última versión de Chrome ({e}); usando versión de respaldo.")
        version, chrome_url = CHROME_VERSION_FALLBACK, CHROME_URL_FALLBACK

    zip_path = BASE_DIR / 'chrome.zip'
    download_file(chrome_url, zip_path)
    _robust_rmtree(CHROME_DIR)
    CHROME_DIR.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(CHROME_DIR)
    zip_path.unlink()
    _write_version_marker(CHROME_DIR, version)
    logger.info(f"Chrome {version} instalado.")
    return str(chrome_exe)

def ensure_chromedriver(force=False):
    driver_exe = CHROMEDRIVER_DIR / 'chromedriver.exe'
    if driver_exe.exists() and not force:
        return str(driver_exe)

    logger.info("Instalando ChromeDriver...")
    try:
        version, _, chromedriver_url = get_latest_stable_info()
    except Exception as e:
        logger.warning(f"No se pudo consultar la última versión de ChromeDriver ({e}); usando versión de respaldo.")
        version, chromedriver_url = CHROME_VERSION_FALLBACK, CHROMEDRIVER_URL_FALLBACK

    zip_path = BASE_DIR / 'chromedriver.zip'
    if zip_path.exists():
        zip_path.unlink()
    _robust_rmtree(CHROMEDRIVER_DIR)

    download_file(chromedriver_url, zip_path)

    temp_extract = CHROMEDRIVER_DIR.parent / 'chromedriver_temp'
    _robust_rmtree(temp_extract)
    temp_extract.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(zip_path, 'r') as zf:
        chromedriver_member = None
        for member in zf.namelist():
            if member.endswith('chromedriver.exe'):
                chromedriver_member = member
                break
        if not chromedriver_member:
            raise Exception("No se encontró chromedriver.exe en el ZIP.")
        zf.extractall(temp_extract)

    extracted = None
    for root, dirs, files in os.walk(temp_extract):
        if 'chromedriver.exe' in files:
            extracted = Path(root) / 'chromedriver.exe'
            break

    if not extracted:
        raise Exception("chromedriver.exe no apareció después de extraer.")

    CHROMEDRIVER_DIR.mkdir(parents=True, exist_ok=True)
    if extracted != driver_exe:
        shutil.move(str(extracted), str(driver_exe))

    _robust_rmtree(temp_extract)
    zip_path.unlink()
    _write_version_marker(CHROMEDRIVER_DIR, version)
    logger.info(f"ChromeDriver {version} instalado correctamente.")
    return str(driver_exe)


def update_chrome_stack():
    """
    Fuerza la reinstalación de Chrome + ChromeDriver con la última versión
    estable disponible. Pensado para llamarse desde un hilo aparte (GUI).
    Devuelve (version, chrome_exe, chromedriver_exe).
    """
    global _latest_stable_cache
    cleanup_orphans()  # cierra chrome/chromedriver colgados ANTES de intentar borrar
    _latest_stable_cache = None  # invalida caché para forzar consulta fresca
    version, _, _ = get_latest_stable_info(use_cache=False)
    chrome_exe = ensure_chrome(force=True)
    chromedriver_exe = ensure_chromedriver(force=True)
    return version, chrome_exe, chromedriver_exe

def cleanup_orphans():
    """Mata procesos de chrome (solo de la carpeta portable) y chromedriver que estén colgados."""
    try:
        import psutil
    except ImportError:
        return

    current_pid = os.getpid()
    chrome_portable_dir = str(CHROME_DIR.resolve()).lower()

    for proc in psutil.process_iter(['pid', 'name', 'exe']):
        try:
            name = (proc.info['name'] or '').lower()
            exe_path = (proc.info['exe'] or '').lower()

            if 'chromedriver' in name:
                if proc.info['pid'] != current_pid:
                    proc.kill()
                    logger.info(f"Limpieza: chromedriver (PID {proc.info['pid']}) terminado.")
            elif 'chrome' in name and chrome_portable_dir in exe_path:
                if proc.info['pid'] != current_pid:
                    proc.kill()
                    logger.info(f"Limpieza: chrome portable (PID {proc.info['pid']}) terminado.")
        except (psutil.NoSuchProcess, psutil.AccessDenied, Exception):
            continue

# ---------------------------------------------------------------------------
# Tesseract OCR (usado por el monitor de Cargas VOIP)
# ---------------------------------------------------------------------------
TESSERACT_RELEASES_API = "https://api.github.com/repos/tesseract-ocr/tesseract/releases/latest"
TESSERACT_ASSET_RE = re.compile(r"^tesseract-ocr-w64-setup-.*\.exe$", re.IGNORECASE)

# Versión "de respaldo" usada únicamente si la consulta a la API de GitHub
# fallara (sin internet, límite de peticiones, cambio de nombre del asset).
TESSERACT_FALLBACK_VERSION = "5.5.3"
TESSERACT_FALLBACK_URL = "https://github.com/tesseract-ocr/tesseract/releases/download/5.5.3/tesseract-ocr-w64-setup-5.5.3.20260724.exe"


def get_latest_tesseract_installer():
    """
    Consulta el último release publicado en GitHub (tesseract-ocr/tesseract)
    y devuelve (version, url) del instalador para Windows x64. Como el
    proyecto sube el instalador como un asset de cada release (no una URL
    fija), consultar 'releases/latest' evita tener que actualizar a mano
    el link cada vez que sale una versión nueva. Si la consulta falla,
    cae a una versión de respaldo fija (puede haber quedado desactualizada).
    """
    import requests
    try:
        resp = requests.get(
            TESSERACT_RELEASES_API, timeout=20,
            headers={"Accept": "application/vnd.github+json"}
        )
        resp.raise_for_status()
        data = resp.json()
        version = data.get("tag_name", "").strip() or "desconocida"
        asset = next(
            (a for a in data.get("assets", []) if TESSERACT_ASSET_RE.match(a.get("name", ""))),
            None
        )
        if not asset:
            raise ValueError("El último release no trae un instalador 'tesseract-ocr-w64-setup-*.exe'.")
        return version, asset["browser_download_url"]
    except Exception as e:
        logger.warning(f"No se pudo consultar la última versión de Tesseract ({e}); usando versión de respaldo.")
        return TESSERACT_FALLBACK_VERSION, TESSERACT_FALLBACK_URL


def find_tesseract_exe(configured_path: str = ""):
    """Busca tesseract.exe: primero la ruta configurada a mano (si sigue
    existiendo), luego el PATH del sistema, luego las ubicaciones típicas
    donde lo deja el instalador oficial."""
    if configured_path and Path(configured_path).is_file():
        return configured_path
    found = shutil.which("tesseract")
    if found:
        return found
    for candidate in (
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    ):
        if Path(candidate).is_file():
            return candidate
    return None


def install_tesseract(progress_cb=None):
    """
    Descarga el instalador oficial más reciente de Tesseract y lo ejecuta.
    Intenta una instalación silenciosa (switches típicos de Inno Setup, que
    es lo que usa este instalador); Windows igual mostrará el aviso de
    permisos de administrador (UAC), ya que instala en Program Files.
    Si por algún motivo el instalador no reconoce esos switches, los
    ignora y simplemente abre su asistente normal.

    Devuelve (version, ruta_tesseract_exe_o_None).
    """
    def log(msg):
        logger.info(msg)
        if progress_cb:
            progress_cb(msg)

    version, url = get_latest_tesseract_installer()
    log(f"Descargando instalador de Tesseract {version}...")

    installer_path = BASE_DIR / "tesseract-installer.exe"
    download_file(url, installer_path)

    log("Ejecutando instalador (puede pedir permisos de administrador)...")
    try:
        subprocess.run(
            [
                str(installer_path),
                "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-",
                r'/DIR=C:\Program Files\Tesseract-OCR',
            ],
            timeout=300,
        )
    finally:
        try:
            installer_path.unlink()
        except Exception:
            pass

    exe_path = find_tesseract_exe()
    if exe_path:
        log(f"Tesseract {version} instalado en: {exe_path}")
    else:
        log("El instalador se ejecutó pero no se encontró tesseract.exe; "
            "si se abrió el asistente, complétalo y vuelve a intentar.")
    return version, exe_path


# ---------------------------------------------------------------------------
# Driver Selenium normal
# ---------------------------------------------------------------------------
from selenium.webdriver import Chrome
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options as ChromeOptions

def get_robust_driver(chrome_exe: str, chromedriver_exe: str, headless: bool = True):
    opts = ChromeOptions()
    opts.binary_location = chrome_exe
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_argument("--start-maximized")
    opts.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36")
    opts.add_argument("--disable-extensions")
    opts.add_argument("--disable-infobars")
    opts.add_argument("--no-default-browser-check")
    opts.add_argument("--no-first-run")
    if headless:
        opts.add_argument("--headless=new")
        opts.add_argument("--disable-gpu")
        opts.add_argument("--window-size=1920,1080")
        opts.add_argument("--disable-dev-shm-usage")
        opts.add_argument("--no-sandbox")
    opts.add_argument("--allow-running-insecure-content")
    opts.add_argument("--ignore-certificate-errors")
    opts.add_argument("--allow-insecure-localhost")
    opts.add_argument("--unsafely-treat-insecure-origin-as-secure=http://178.105.24.84,http://158.69.177.101,http://clientes.datavoice.com.co,http://45.226.115.82")

    service = Service(executable_path=chromedriver_exe)
    driver = Chrome(service=service, options=opts)
    logger.info("Driver creado con opciones del script original")
    return driver

# ---------------------------------------------------------------------------
# Manejo de configuración
# ---------------------------------------------------------------------------
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

# ---------------------------------------------------------------------------
# Carga dinámica de proveedores
# ---------------------------------------------------------------------------
def load_providers_from_dir(directory: Path) -> List:
    from base_provider import BaseProvider
    providers = []
    if not directory.exists():
        return providers
    sys.path.insert(0, str(directory.parent))
    for f in directory.glob("*.py"):
        if f.stem == "__init__" or f.stem == "vos_helpers":
            continue
        mod_name = f"providers.{f.stem}"
        spec = importlib.util.spec_from_file_location(mod_name, str(f))
        if spec is None:
            continue
        mod = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(mod)
        except Exception as e:
            logger.warning(f"No se pudo cargar {f.name}: {e}")
            continue
        for attr_name in dir(mod):
            attr = getattr(mod, attr_name)
            if isinstance(attr, type) and issubclass(attr, BaseProvider) and attr != BaseProvider:
                providers.append(attr())
    return providers

def get_all_providers():
    internal = INTERNAL_DIR / 'providers'
    external = BASE_DIR / 'providers'
    providers = load_providers_from_dir(internal)
    nombres = {p.name for p in providers}
    for p in load_providers_from_dir(external):
        if p.name not in nombres:
            providers.append(p)

    # Proveedores genéricos creados desde la GUI (botón "Agregar proveedor")
    try:
        from generic_provider import GenericWebProvider
        cfg = load_config()
        for custom in cfg.get("custom_providers", []):
            nombre = str(custom.get("name", "")).strip()
            if nombre and nombre not in nombres:
                providers.append(GenericWebProvider(custom))
                nombres.add(nombre)
    except Exception as e:
        logger.warning(f"No se pudieron cargar proveedores genéricos: {e}")

    return providers


def get_custom_provider_names(config: dict) -> set:
    """Nombres de proveedores creados con el formulario 'Agregar proveedor'."""
    return {str(c.get("name", "")).strip() for c in config.get("custom_providers", []) if c.get("name")}

import providers.vos_helpers

# ---------------------------------------------------------------------------
# Orden de proveedores
# ---------------------------------------------------------------------------
def sort_providers_by_order(providers: List, config: dict) -> List:
    order = config.get("provider_order", [])
    order = [str(o).strip() for o in order]
    if not order:
        order = [p.name for p in providers]
        config["provider_order"] = order
        save_config(config)
    else:
        existing = set(order)
        for p in providers:
            if p.name not in existing:
                order.append(p.name)
        config["provider_order"] = order
        save_config(config)

    def sort_key(provider):
        try:
            return order.index(provider.name)
        except ValueError:
            return len(order)
    providers.sort(key=sort_key)
    return providers

# ---------------------------------------------------------------------------
# Lanzar proceso hijo correctamente
# ---------------------------------------------------------------------------
def _get_launch_cmd(args: List[str]) -> List[str]:
    if getattr(sys, 'frozen', False):
        return [sys.executable] + args
    else:
        return [sys.executable, os.path.abspath(__file__)] + args

# ---------------------------------------------------------------------------
# Ciclo de consulta de saldos
# ---------------------------------------------------------------------------
def run_balance_cycle(config: dict):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

    cred_path = Path(config['credentials_path'])
    if not cred_path.exists():
        print(f"ERROR: Credenciales no encontradas en {cred_path}", flush=True)
        return

    cleanup_orphans()

    if verificar_bloqueo():
        print("\n⚠️  APLICACIÓN BLOQUEADA POR EL ADMINISTRADOR.", flush=True)
        print("   Contacte al soporte para más información.\n", flush=True)
        return

    from oauth2client.service_account import ServiceAccountCredentials
    import gspread

    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_name(str(cred_path), scope)
    client = gspread.authorize(creds)
    sh = client.open_by_url(config['google_sheet_url']).sheet1

    chrome_exe = ensure_chrome()
    chromedriver_exe = ensure_chromedriver()

    providers = get_all_providers()
    providers = sort_providers_by_order(providers, config)

    enabled_names = config.get("enabled_providers", [])
    if not enabled_names:
        enabled_names = [p.name for p in providers]
        config["enabled_providers"] = enabled_names
        save_config(config)

    print("=" * 50, flush=True)
    print("  CHEQUEO DE SALDOS", flush=True)
    print(f"  {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}", flush=True)
    print("=" * 50, flush=True)

    for provider in providers:
        if provider.name not in enabled_names:
            continue
        prov_cfg = config.get("providers_config", {}).get(provider.name, {})
        final_cfg = {}
        for field in provider.config_fields:
            key = field["key"]
            final_cfg[key] = prov_cfg.get(key, field.get("default", ""))

        # --- Aplicar celda personalizada si existe en la configuración ---
        if 'sheet_row' in prov_cfg:
            provider.sheet_row = int(prov_cfg['sheet_row'])
        if 'sheet_col' in prov_cfg:
            provider.sheet_col = int(prov_cfg['sheet_col'])

        print(f"\nProcesando {provider.name}...", flush=True)

        visibles = [str(v).strip() for v in config.get("visible_providers", [])]
        quiere_visible = provider.name in visibles

        MAX_INTENTOS = 2
        success, msg = False, ""
        for intento in range(1, MAX_INTENTOS + 1):
            try:
                success, msg = provider.get_balance(
                    final_cfg, sh, config['google_sheet_url'],
                    driver_paths={"chrome_exe": chrome_exe, "chromedriver_exe": chromedriver_exe},
                    get_driver_fn=get_robust_driver,
                    headless=not quiere_visible
                )
            except TypeError:
                # Este proveedor todavía no acepta el kwarg 'headless'
                # (p. ej. providers viejos sin actualizar).
                try:
                    success, msg = provider.get_balance(
                        final_cfg, sh, config['google_sheet_url'],
                        driver_paths={"chrome_exe": chrome_exe, "chromedriver_exe": chromedriver_exe},
                        get_driver_fn=get_robust_driver
                    )
                except Exception as e:
                    success, msg = False, str(e)
            except Exception as e:
                success, msg = False, str(e)

            if success:
                break
            if intento < MAX_INTENTOS:
                print(f"  ⚠️  {provider.name} -> intento {intento} falló ({msg}); reintentando...", flush=True)
                time.sleep(2)

        if success:
            print(f"  ✅ {provider.name} -> OK ({msg})", flush=True)
        else:
            print(f"  ❌ {provider.name} -> FALLO tras {MAX_INTENTOS} intento(s): {msg}", flush=True)

    print("\n" + "=" * 50, flush=True)
    print("  TODAS LAS TAREAS COMPLETADAS", flush=True)
    print("=" * 50, flush=True)

    try:
        sh.update_cell(5, 7, datetime.now().strftime("%H:%M:%S"))
    except Exception:
        pass

    print("\nProceso finalizado. Puede cerrar esta ventana.", flush=True)
    try:
        input()
    except (EOFError, OSError):
        pass

# ---------------------------------------------------------------------------
# Planificador
# ---------------------------------------------------------------------------
def run_scheduler():
    config = load_config()
    sh = None
    try:
        if Path(config['credentials_path']).exists():
            from oauth2client.service_account import ServiceAccountCredentials
            import gspread
            scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
            creds = ServiceAccountCredentials.from_json_keyfile_name(str(config['credentials_path']), scope)
            client = gspread.authorize(creds)
            sh = client.open_by_url(config['google_sheet_url']).sheet1
    except Exception:
        pass

    owner_id = f"{socket.gethostname()}_{uuid.uuid4().hex[:8]}"
    executed_today = set()
    current_date = datetime.now().date()

    logger.info("Scheduler iniciado.")
    while True:
        ahora = datetime.now()
        if ahora.date() != current_date:
            executed_today.clear()
            current_date = ahora.date()

        weekday = ahora.weekday()
        if weekday in (0, 1, 2, 3, 4):
            horarios = config['horarios_lun_vie']
        elif weekday == 5:
            horarios = config['horarios_sabado']
        else:
            horarios = []

        for hhmm in horarios:
            try:
                h, m = map(int, hhmm.split(':'))
                target = ahora.replace(hour=h, minute=m, second=0, microsecond=0)
                if abs((ahora - target).total_seconds()) > config['tolerancia_min'] * 60:
                    continue
                if hhmm in executed_today:
                    continue

                if sh:
                    try:
                        lock_val = f"{owner_id}|{ahora.isoformat()}"
                        sh.update_acell('Z1', lock_val)
                        read_back = sh.acell('Z1').value
                        if not read_back or read_back.split('|')[0] != owner_id:
                            continue
                    except Exception:
                        pass

                logger.info(f"Lanzando tarea programada para {hhmm}")
                subprocess.Popen(
                    _get_launch_cmd(['--balance']),
                    creationflags=subprocess.CREATE_NEW_CONSOLE
                )
                executed_today.add(hhmm)

                if sh:
                    time.sleep(5)
                    try:
                        sh.update_acell('Z1', '')
                    except Exception:
                        pass
            except Exception as e:
                logger.error(f"Error en horario {hhmm}: {e}")

        time.sleep(config['sleep_interval'])

# ---------------------------------------------------------------------------
# Interfaz gráfica
# ---------------------------------------------------------------------------
def run_gui():
    import tkinter as tk
    from tkinter import ttk, messagebox, filedialog
    import webbrowser
    import ctypes

    root = tk.Tk()
    root.title("Saldos Scheduler")
    root.geometry("835x600")
    root.minsize(850, 500)
    root.configure(bg="#1e1e2e")

    # Centrar ventana
    root.update_idletasks()
    w = root.winfo_width()
    h = root.winfo_height()
    sw = root.winfo_screenwidth()
    sh = root.winfo_screenheight()
    x = (sw - w) // 2
    y = (sh - h) // 2
    root.geometry(f"+{x}+{y}")

    # Aplicar tema oscuro a la barra de título de Windows
    def aplicar_tema_oscuro_ventana():
        try:
            hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
            # DWMWA_USE_IMMERSIVE_DARK_MODE = 20 (Windows 10 20H1+, Windows 11)
            valor = ctypes.c_int(2)   # 2 = oscuro
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(valor), ctypes.sizeof(valor))
        except Exception:
            pass
    aplicar_tema_oscuro_ventana()

    # Estilo oscuro
    style = ttk.Style()
    style.theme_use("clam")

    BG = "#1e1e2e"
    FG = "#cdd6f4"
    ACCENT = "#89b4fa"
    ACCENT_HOVER = "#74c7ec"
    DARKER = "#181825"
    ENTRY_BG = "#313244"
    BUTTON_BG = "#45475a"

    style.configure(".", background=BG, foreground=FG, font=("Segoe UI", 10))
    style.configure("TFrame", background=BG)
    style.configure("TLabel", background=BG, foreground=FG)
    style.configure("TLabelframe", background=BG, foreground=FG, borderwidth=1, relief="solid", bordercolor="#585b70")
    style.configure("TLabelframe.Label", background=BG, foreground=ACCENT, font=("Segoe UI", 11, "bold"))
    style.configure("TNotebook", background=BG, borderwidth=0)
    style.configure("TNotebook.Tab", background=DARKER, foreground=FG, padding=[18, 8], font=("Segoe UI", 10))
    style.map("TNotebook.Tab",
              background=[("selected", ACCENT), ("active", "#45475a")],
              foreground=[("selected", "#1e1e2e")],
              padding=[("selected", [22, 10])])  # Pestaña más grande al seleccionar

    style.configure("Accent.TButton", background=ACCENT, foreground="#1e1e2e", borderwidth=0, padding=[15, 6], font=("Segoe UI", 10, "bold"))
    style.map("Accent.TButton", background=[("active", ACCENT_HOVER), ("disabled", "#585b70")])

    style.configure("TButton", background=BUTTON_BG, foreground=FG, borderwidth=0, padding=[10, 5])
    style.map("TButton", background=[("active", "#585b70")])

    style.configure("Treeview", background=ENTRY_BG, fieldbackground=ENTRY_BG, foreground=FG, rowheight=30, borderwidth=0)
    style.configure("Treeview.Heading", background=DARKER, foreground=ACCENT, font=("Segoe UI", 10, "bold"), borderwidth=0)
    style.map("Treeview", background=[("selected", ACCENT)], foreground=[("selected", "#1e1e2e")])

    style.configure("TEntry", fieldbackground=ENTRY_BG, foreground=FG, borderwidth=1, relief="solid", bordercolor="#585b70")

    # --- Combobox: evita el "flash" blanco al abrir/seleccionar una opción ---
    style.configure("TCombobox",
                     fieldbackground=ENTRY_BG, background=ENTRY_BG, foreground=FG,
                     arrowcolor=FG, bordercolor="#585b70", lightcolor=ENTRY_BG, darkcolor=ENTRY_BG,
                     selectbackground=ENTRY_BG, selectforeground=FG, insertcolor=FG)
    style.map("TCombobox",
              fieldbackground=[("readonly", ENTRY_BG), ("disabled", ENTRY_BG), ("!disabled", ENTRY_BG)],
              foreground=[("readonly", FG), ("disabled", "#6c7086")],
              background=[("readonly", ENTRY_BG), ("active", BUTTON_BG)],
              selectbackground=[("readonly", ENTRY_BG)],
              selectforeground=[("readonly", FG)])
    # El menú desplegable (popdown) de Combobox no es un widget ttk, así que se
    # colorea aparte con option_add para que combine con el tema oscuro.
    root.option_add("*TCombobox*Listbox.background", ENTRY_BG)
    root.option_add("*TCombobox*Listbox.foreground", FG)
    root.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
    root.option_add("*TCombobox*Listbox.selectForeground", "#1e1e2e")
    root.option_add("*TCombobox*Listbox.font", ("Segoe UI", 10))

    scheduler_process = None
    scheduler_status_var = tk.StringVar(value="⚫  Detenido")
    cargas_voip_process = None
    cargas_voip_status_var = tk.StringVar(value="⚫  Detenido")
    auto_like_process = None
    auto_like_status_var = tk.StringVar(value="⚫  Detenido")
    config = load_config()

    def verificar_y_avisar():
        if verificar_bloqueo():
            messagebox.showwarning(
                "Aplicación bloqueada",
                "⚠️  Esta aplicación ha sido bloqueada por el administrador.\n\n"
                "Contacte al soporte para más información."
            )
    root.after(150, verificar_y_avisar)

    # ====================== NOTEBOOK ======================
    notebook = ttk.Notebook(root)
    notebook.pack(fill="both", expand=True, padx=15, pady=(15, 5))

    # ------------------------------------------------------------------
    # 1. PESTAÑA PROVEEDORES (tabla + botones)
    # ------------------------------------------------------------------
    tab_prov = ttk.Frame(notebook)
    notebook.add(tab_prov, text="   Proveedores   ")

    main_frame = ttk.Frame(tab_prov)
    main_frame.pack(fill="both", expand=True, padx=5, pady=5)

    # Proveedores que SIEMPRE corren en modo visible por requerir intervención
    # visual obligatoria (captcha), y por lo tanto no aplica alternar la opción.
    PROVEEDORES_VISIBLE_FORZADO = {"SipMovil", "1980"}

    columns = ("Proveedor", "Visible")
    tree = ttk.Treeview(main_frame, columns=columns, show="tree headings", selectmode="extended")
    tree.heading("#0", text="Estado")
    tree.heading("Proveedor", text="Proveedor")
    tree.heading("Visible", text="Visible")
    tree.column("#0", width=60, anchor="center")
    tree.column("Proveedor", width=230, anchor="w")
    tree.column("Visible", width=70, anchor="center")
    tree.pack(side="left", fill="both", expand=True)

    scrollbar = ttk.Scrollbar(main_frame, orient="vertical", command=tree.yview)
    scrollbar.pack(side="right", fill="y")
    tree.configure(yscrollcommand=scrollbar.set)

    btn_panel = ttk.Frame(main_frame)
    btn_panel.pack(side="right", fill="y", padx=(15, 5))

    def refresh_tree():
        tree.delete(*tree.get_children())
        providers = get_all_providers()
        providers = sort_providers_by_order(providers, config)
        enabled = [str(e).strip() for e in config.get("enabled_providers", [])]
        visibles = [str(v).strip() for v in config.get("visible_providers", [])]
        for p in providers:
            nombre_limpio = str(p.name).strip()
            icon = "✓" if nombre_limpio in enabled else "✗"
            if nombre_limpio in PROVEEDORES_VISIBLE_FORZADO:
                icono_visible = "👁 (fijo)"
            elif nombre_limpio in visibles:
                icono_visible = "👁"
            else:
                icono_visible = "—"
            tree.insert("", "end", text=icon, values=(nombre_limpio, icono_visible))

    refresh_tree()

    def configurar_proveedor():
        selecciones = tree.selection()
        if not selecciones:
            messagebox.showinfo("Seleccionar", "Seleccione un proveedor primero.")
            return
        sel = selecciones[0]
        item = tree.item(sel)
        nombre = str(item["values"][0]).strip()
        providers = get_all_providers()
        provider = next((p for p in providers if str(p.name).strip() == nombre), None)
        if not provider:
            return

        win = tk.Toplevel(root)
        win.title(f"Configurar {provider.name}")
        alto = 140 + (len(provider.config_fields) + 3) * 48
        win.geometry(f"420x{min(max(alto, 300), 560)}")
        win.configure(bg=BG)
        win.transient(root)
        win.grab_set()
        entries = {}
        prov_cfg = config.get("providers_config", {}).get(provider.name, {})

        for idx, field in enumerate(provider.config_fields):
            ttk.Label(win, text=field["label"], font=("Segoe UI", 10)).grid(row=idx, column=0, sticky="w", padx=15, pady=8)
            var = tk.StringVar(value=prov_cfg.get(field["key"], field.get("default", "")))
            ancho = 38 if field["key"] == "url" else 30
            ttk.Entry(win, textvariable=var, width=ancho).grid(row=idx, column=1, padx=15, pady=8)
            entries[field["key"]] = var

        row_offset = len(provider.config_fields)
        ttk.Label(win, text="Fila (Sheet Row)", font=("Segoe UI", 10)).grid(row=row_offset, column=0, sticky="w", padx=15, pady=8)
        var_row = tk.StringVar(value=str(prov_cfg.get("sheet_row", provider.sheet_row)))
        ttk.Entry(win, textvariable=var_row, width=10).grid(row=row_offset, column=1, padx=15, pady=8, sticky="w")
        entries["sheet_row"] = var_row

        ttk.Label(win, text="Columna (Sheet Col)", font=("Segoe UI", 10)).grid(row=row_offset+1, column=0, sticky="w", padx=15, pady=8)
        var_col = tk.StringVar(value=str(prov_cfg.get("sheet_col", provider.sheet_col)))
        ttk.Entry(win, textvariable=var_col, width=10).grid(row=row_offset+1, column=1, padx=15, pady=8, sticky="w")
        entries["sheet_col"] = var_col

        def guardar():
            new_cfg = {}
            for k, v in entries.items():
                val = v.get()
                if k in ("sheet_row", "sheet_col"):
                    try:
                        new_cfg[k] = int(val)
                    except ValueError:
                        new_cfg[k] = val
                else:
                    new_cfg[k] = val
            config.setdefault("providers_config", {})[provider.name] = new_cfg
            save_config(config)
            win.destroy()

        ttk.Button(win, text="Guardar", command=guardar, style="Accent.TButton").grid(
            row=row_offset+2, column=1, pady=20, sticky="e", padx=15
        )

    def toggle_proveedor():
        selecciones = tree.selection()
        if not selecciones:
            messagebox.showinfo("Seleccionar", "Seleccione al menos un proveedor.")
            return

        nombres_seleccionados = []
        for sel in selecciones:
            try:
                item = tree.item(sel)
                nombre = str(item["values"][0]).strip()
                nombres_seleccionados.append(nombre)
            except Exception:
                continue

        if not nombres_seleccionados:
            return

        enabled = [str(e).strip() for e in config.get("enabled_providers", [])]
        accion_habilitar = nombres_seleccionados[0] not in enabled

        for nombre in nombres_seleccionados:
            if accion_habilitar:
                if nombre not in enabled:
                    enabled.append(nombre)
            else:
                if nombre in enabled:
                    enabled.remove(nombre)

        config["enabled_providers"] = enabled
        save_config(config)
        refresh_tree()

        for nombre in nombres_seleccionados:
            for child in tree.get_children():
                if str(tree.item(child)["values"][0]).strip() == nombre:
                    tree.selection_add(child)

    def toggle_visible():
        """Alterna el modo 'visible' (headless=False) para los proveedores
        seleccionados. No aplica a los que requieren captcha (SipMovil, 1980),
        ya que esos siempre corren visibles de forma fija."""
        selecciones = tree.selection()
        if not selecciones:
            messagebox.showinfo("Seleccionar", "Seleccione al menos un proveedor.")
            return

        nombres_seleccionados = []
        for sel in selecciones:
            nombre = str(tree.item(sel)["values"][0]).strip()
            if nombre in PROVEEDORES_VISIBLE_FORZADO:
                continue
            nombres_seleccionados.append(nombre)

        if not nombres_seleccionados:
            messagebox.showinfo(
                "No aplica",
                "Los proveedores seleccionados ya corren siempre en modo visible "
                "(requieren captcha) y no se pueden alternar."
            )
            return

        visibles = [str(v).strip() for v in config.get("visible_providers", [])]
        activar = nombres_seleccionados[0] not in visibles

        for nombre in nombres_seleccionados:
            if activar:
                if nombre not in visibles:
                    visibles.append(nombre)
            else:
                if nombre in visibles:
                    visibles.remove(nombre)

        config["visible_providers"] = visibles
        save_config(config)
        refresh_tree()

        for nombre in nombres_seleccionados:
            for child in tree.get_children():
                if str(tree.item(child)["values"][0]).strip() == nombre:
                    tree.selection_add(child)

    def probar_ahora_seleccionado():
        """Corre el proveedor seleccionado (predeterminado o personalizado)
        una sola vez, en modo visible, para poder ver el paso a paso y
        diagnosticar visualmente el error."""
        selecciones = tree.selection()
        if not selecciones:
            messagebox.showinfo("Seleccionar", "Seleccione un proveedor primero.")
            return
        if not Path(config.get("credentials_path", "")).exists():
            messagebox.showerror("Error", "Configura primero las credenciales de Google (pestaña Configuración).")
            return

        nombre = str(tree.item(selecciones[0])["values"][0]).strip()
        provider = next((p for p in get_all_providers() if str(p.name).strip() == nombre), None)
        if not provider:
            return

        prov_cfg = config.get("providers_config", {}).get(provider.name, {})
        test_cfg = {}
        for field in provider.config_fields:
            key = field["key"]
            test_cfg[key] = prov_cfg.get(key, field.get("default", ""))

        btn_probar_sel.config(state="disabled", text="Probando...")
        root.update_idletasks()

        def tarea():
            try:
                chrome_exe = ensure_chrome()
                chromedriver_exe = ensure_chromedriver()
                try:
                    ok, msg = provider.get_balance(
                        test_cfg, None, "",
                        driver_paths={"chrome_exe": chrome_exe, "chromedriver_exe": chromedriver_exe},
                        get_driver_fn=get_robust_driver,
                        headless=False
                    )
                except TypeError:
                    # Proveedor que aún no acepta 'headless' como parámetro
                    # (p. ej. SipMovil/1980, que ya corren visibles de forma fija).
                    ok, msg = provider.get_balance(
                        test_cfg, None, "",
                        driver_paths={"chrome_exe": chrome_exe, "chromedriver_exe": chromedriver_exe},
                        get_driver_fn=get_robust_driver
                    )
            except Exception as e:
                ok, msg = False, str(e)

            def mostrar():
                btn_probar_sel.config(state="normal", text="🧪  Probar ahora (visible)")
                if ok:
                    messagebox.showinfo("Prueba exitosa", f"Saldo detectado: {msg}\n\n"
                                         "(No se escribió en la hoja, esto fue solo una prueba.)")
                else:
                    messagebox.showerror("Prueba fallida", f"No se pudo leer el saldo:\n{msg}")
            root.after(0, mostrar)

        threading.Thread(target=tarea, daemon=True).start()

    def mover_arriba():
        selecciones = tree.selection()
        if not selecciones:
            return
        sel = selecciones[0]
        item = tree.item(sel)
        nombre = str(item["values"][0]).strip()
        order = config.get("provider_order", [])
        order = [str(o).strip() for o in order]

        if nombre not in order:
            order.append(nombre)
            config["provider_order"] = order
            save_config(config)
            refresh_tree()
            order = config.get("provider_order", [])
            order = [str(o).strip() for o in order]

        if nombre not in order:
            return

        idx = order.index(nombre)
        if idx > 0:
            order[idx], order[idx-1] = order[idx-1], order[idx]
            config["provider_order"] = order
            save_config(config)
            refresh_tree()
            for child in tree.get_children():
                if str(tree.item(child)["values"][0]).strip() == nombre:
                    tree.selection_set(child)
                    tree.focus(child)
                    break

    def mover_abajo():
        selecciones = tree.selection()
        if not selecciones:
            return
        sel = selecciones[0]
        item = tree.item(sel)
        nombre = str(item["values"][0]).strip()
        order = config.get("provider_order", [])
        order = [str(o).strip() for o in order]

        if nombre not in order:
            order.append(nombre)
            config["provider_order"] = order
            save_config(config)
            refresh_tree()
            order = config.get("provider_order", [])
            order = [str(o).strip() for o in order]

        if nombre not in order:
            return

        idx = order.index(nombre)
        if idx < len(order) - 1:
            order[idx], order[idx+1] = order[idx+1], order[idx]
            config["provider_order"] = order
            save_config(config)
            refresh_tree()
            for child in tree.get_children():
                if str(tree.item(child)["values"][0]).strip() == nombre:
                    tree.selection_set(child)
                    tree.focus(child)
                    break

    def abrir_formulario_proveedor(nombre_original=None):
        """Abre el formulario de proveedor genérico. Si nombre_original viene
        dado, precarga los datos existentes de ese proveedor y guarda como
        edición (en vez de crear uno nuevo)."""
        datos_existentes = {}
        if nombre_original:
            for c in config.get("custom_providers", []):
                if c.get("name") == nombre_original:
                    datos_existentes = dict(c)
                    break

        win = tk.Toplevel(root)
        win.title(f"Editar proveedor: {nombre_original}" if nombre_original else "Agregar proveedor")
        win.geometry("560x640")
        win.configure(bg=BG)
        win.transient(root)
        win.grab_set()

        canvas = tk.Canvas(win, bg=BG, highlightthickness=0)
        vscroll = ttk.Scrollbar(win, orient="vertical", command=canvas.yview)
        form = ttk.Frame(canvas)
        form_window = canvas.create_window((0, 0), window=form, anchor="nw")
        form.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        # El frame interno debe ocupar todo el ancho del canvas (para que se
        # vea bien al redimensionar la ventana).
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(form_window, width=e.width))
        canvas.configure(yscrollcommand=vscroll.set)
        canvas.pack(side="left", fill="both", expand=True)
        vscroll.pack(side="right", fill="y")

        # --- Scroll con la rueda del mouse (Windows: <MouseWheel>) ---
        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        def _bind_mousewheel(_event=None):
            canvas.bind_all("<MouseWheel>", _on_mousewheel)

        def _unbind_mousewheel(_event=None):
            canvas.unbind_all("<MouseWheel>")

        canvas.bind("<Enter>", _bind_mousewheel)
        canvas.bind("<Leave>", _unbind_mousewheel)
        win.bind("<Destroy>", lambda e: _unbind_mousewheel())

        vars_ = {}
        row = [0]

        def add_field(label, key, default="", width=40):
            valor = datos_existentes.get(key, default)
            ttk.Label(form, text=label, font=("Segoe UI", 10)).grid(
                row=row[0], column=0, sticky="w", padx=15, pady=6)
            var = tk.StringVar(value=valor)
            ttk.Entry(form, textvariable=var, width=width).grid(
                row=row[0], column=1, padx=15, pady=6, sticky="w")
            vars_[key] = var
            row[0] += 1
            return var

        def add_selector_combo(label, key, default_type="name"):
            valor = datos_existentes.get(key, default_type)
            ttk.Label(form, text=label, font=("Segoe UI", 10)).grid(
                row=row[0], column=0, sticky="w", padx=15, pady=6)
            var = tk.StringVar(value=valor)
            combo = ttk.Combobox(form, textvariable=var, values=["name", "id", "css", "xpath"],
                                  width=10, state="readonly")
            combo.grid(row=row[0], column=1, padx=15, pady=6, sticky="w")
            vars_[key] = var
            row[0] += 1
            return var

        ttk.Label(form, text="Datos del sitio", font=("Segoe UI", 11, "bold"),
                  foreground=ACCENT).grid(row=row[0], column=0, columnspan=2, sticky="w", padx=15, pady=(10, 4))
        row[0] += 1

        add_field("Nombre del proveedor *", "name")
        add_field("URL de inicio de sesión *", "url", width=48)
        add_field("Usuario (por defecto)", "usuario_default")
        add_field("Contraseña (por defecto)", "password_default")

        ttk.Label(form, text="Selector campo Usuario", font=("Segoe UI", 11, "bold"),
                  foreground=ACCENT).grid(row=row[0], column=0, columnspan=2, sticky="w", padx=15, pady=(14, 4))
        row[0] += 1
        add_selector_combo("Tipo", "user_selector_type", "name")
        add_field("Valor (ej: username)", "user_selector")

        ttk.Label(form, text="Selector campo Contraseña", font=("Segoe UI", 11, "bold"),
                  foreground=ACCENT).grid(row=row[0], column=0, columnspan=2, sticky="w", padx=15, pady=(14, 4))
        row[0] += 1
        add_selector_combo("Tipo", "pass_selector_type", "name")
        add_field("Valor (ej: password)", "pass_selector")

        ttk.Label(form, text="Botón enviar (opcional; si se deja vacío, se usa ENTER)",
                  font=("Segoe UI", 11, "bold"), foreground=ACCENT).grid(
            row=row[0], column=0, columnspan=2, sticky="w", padx=15, pady=(14, 4))
        row[0] += 1
        add_selector_combo("Tipo", "submit_selector_type", "css")
        add_field("Valor (ej: button[type=submit])", "submit_selector")

        ttk.Label(form, text="Dónde leer el saldo", font=("Segoe UI", 11, "bold"),
                  foreground=ACCENT).grid(row=row[0], column=0, columnspan=2, sticky="w", padx=15, pady=(14, 4))
        row[0] += 1
        add_selector_combo("Tipo", "balance_selector_type", "xpath")
        add_field("Valor (ej: //span[@class='balance'])", "balance_selector", width=48)
        add_field("Regex de extracción (opcional)", "balance_regex", default=DEFAULT_BALANCE_REGEX, width=48)
        add_field("Prefijo (ej: '$ ')", "prefix")
        add_field("Sufijo (ej: ' USD')", "suffix")
        add_field("Espera tras login (segundos)", "wait_after_login", default="1.5", width=10)
        add_field("Timeout de carga (segundos)", "timeout", default="30", width=10)

        ttk.Label(form, text="Ubicación en la hoja de cálculo", font=("Segoe UI", 11, "bold"),
                  foreground=ACCENT).grid(row=row[0], column=0, columnspan=2, sticky="w", padx=15, pady=(14, 4))
        row[0] += 1
        add_field("Fila (Sheet Row)", "sheet_row", default="1", width=10)
        add_field("Columna (Sheet Col)", "sheet_col", default="1", width=10)

        aviso = ("Nota: este formulario cubre sitios con login simple de usuario/contraseña "
                 "en una sola página. Sitios con captcha, varios pasos o iframes anidados "
                 "todavía requieren un archivo .py a medida en la carpeta 'providers'.")
        ttk.Label(form, text=aviso, foreground="#9399b2", wraplength=440, justify="left",
                  font=("Segoe UI", 9)).grid(row=row[0], column=0, columnspan=2, sticky="w", padx=15, pady=(14, 4))
        row[0] += 1

        def leer_definicion():
            nombre = vars_["name"].get().strip()
            url = vars_["url"].get().strip()
            if not nombre or not url:
                messagebox.showerror("Faltan datos", "El nombre y la URL son obligatorios.")
                return None
            if not vars_["user_selector"].get().strip() or not vars_["pass_selector"].get().strip():
                messagebox.showerror("Faltan datos", "Debes indicar el selector de usuario y de contraseña.")
                return None
            if not vars_["balance_selector"].get().strip():
                messagebox.showerror("Faltan datos", "Debes indicar el selector donde está el saldo.")
                return None
            try:
                sheet_row = int(vars_["sheet_row"].get().strip())
                sheet_col = int(vars_["sheet_col"].get().strip())
            except ValueError:
                messagebox.showerror("Error", "Fila y columna deben ser números.")
                return None

            return {
                "name": nombre,
                "url": url,
                "usuario_default": vars_["usuario_default"].get(),
                "password_default": vars_["password_default"].get(),
                "user_selector_type": vars_["user_selector_type"].get(),
                "user_selector": vars_["user_selector"].get().strip(),
                "pass_selector_type": vars_["pass_selector_type"].get(),
                "pass_selector": vars_["pass_selector"].get().strip(),
                "submit_selector_type": vars_["submit_selector_type"].get(),
                "submit_selector": vars_["submit_selector"].get().strip(),
                "balance_selector_type": vars_["balance_selector_type"].get(),
                "balance_selector": vars_["balance_selector"].get().strip(),
                "balance_regex": vars_["balance_regex"].get().strip() or DEFAULT_BALANCE_REGEX,
                "prefix": vars_["prefix"].get(),
                "suffix": vars_["suffix"].get(),
                "wait_after_login": vars_["wait_after_login"].get().strip() or "1.5",
                "timeout": vars_["timeout"].get().strip() or "30",
                "sheet_row": sheet_row,
                "sheet_col": sheet_col,
            }

        def probar_ahora():
            definicion = leer_definicion()
            if not definicion:
                return
            if not Path(config.get("credentials_path", "")).exists():
                messagebox.showerror("Error", "Configura primero las credenciales de Google (pestaña Configuración).")
                return

            btn_probar.config(state="disabled", text="Probando...")
            win.update_idletasks()

            def tarea():
                from generic_provider import GenericWebProvider
                try:
                    chrome_exe = ensure_chrome()
                    chromedriver_exe = ensure_chromedriver()
                    provider = GenericWebProvider(definicion)
                    test_cfg = {
                        "usuario": definicion.get("usuario_default", ""),
                        "password": definicion.get("password_default", ""),
                    }
                    ok, msg = provider.get_balance(
                        test_cfg, None, "",
                        driver_paths={"chrome_exe": chrome_exe, "chromedriver_exe": chromedriver_exe},
                        get_driver_fn=get_robust_driver,
                        headless=False
                    )
                except Exception as e:
                    ok, msg = False, str(e)

                def mostrar():
                    btn_probar.config(state="normal", text="🧪  Probar ahora")
                    if ok:
                        messagebox.showinfo("Prueba exitosa", f"Saldo detectado: {msg}\n\n"
                                             "(No se escribió en la hoja, esto fue solo una prueba de conexión.)")
                    else:
                        messagebox.showerror("Prueba fallida", f"No se pudo leer el saldo:\n{msg}")
                win.after(0, mostrar)

            threading.Thread(target=tarea, daemon=True).start()

        def guardar_proveedor():
            definicion = leer_definicion()
            if not definicion:
                return
            existentes = config.setdefault("custom_providers", [])
            nombres_todos = {p.name for p in get_all_providers()}
            nombre_nuevo = definicion["name"]

            # Si estamos editando y el usuario NO cambió el nombre, se excluye
            # a sí mismo de la validación de duplicados; si SÍ lo cambió,
            # solo se excluye el nombre original.
            colision = nombre_nuevo in nombres_todos and nombre_nuevo != nombre_original
            if colision:
                messagebox.showerror("Nombre repetido", "Ya existe un proveedor con ese nombre.")
                return

            # Quita cualquier entrada previa con el nombre original (edición)
            # o con el nuevo nombre (por si acaso), y agrega la definición actualizada.
            existentes[:] = [c for c in existentes
                              if c.get("name") not in (nombre_original, nombre_nuevo)]
            existentes.append(definicion)

            # Si se renombró el proveedor, actualiza las referencias existentes
            # conservando su posición y estado (habilitado / orden / config extra).
            if nombre_original and nombre_original != nombre_nuevo:
                enabled = config.setdefault("enabled_providers", [])
                config["enabled_providers"] = [nombre_nuevo if n == nombre_original else n for n in enabled]

                order = config.setdefault("provider_order", [])
                config["provider_order"] = [nombre_nuevo if n == nombre_original else n for n in order]

                providers_cfg = config.setdefault("providers_config", {})
                if nombre_original in providers_cfg:
                    providers_cfg[nombre_nuevo] = providers_cfg.pop(nombre_original)

            save_config(config)

            enabled = config.setdefault("enabled_providers", [])
            if nombre_nuevo not in enabled:
                enabled.append(nombre_nuevo)
            order = config.setdefault("provider_order", [])
            if nombre_nuevo not in order:
                order.append(nombre_nuevo)
            save_config(config)

            accion = "actualizado" if nombre_original else "guardado y habilitado"
            messagebox.showinfo("Guardado", f"Proveedor '{nombre_nuevo}' {accion}.")
            win.destroy()
            refresh_tree()

        btns = ttk.Frame(form)
        btns.grid(row=row[0], column=0, columnspan=2, pady=(18, 4), padx=15, sticky="ew")
        btn_probar = ttk.Button(btns, text="🧪  Probar ahora", command=probar_ahora)
        btn_probar.pack(side="left", padx=(0, 8))
        ttk.Button(btns, text="💾  Guardar", command=guardar_proveedor, style="Accent.TButton").pack(side="left")
        row[0] += 1
        ttk.Label(form, text="La prueba abre el navegador visible para que puedas ver qué hace paso a paso.\n"
                              "Al guardar, la ejecución real siempre corre oculta, como los demás proveedores.",
                  foreground="#9399b2", font=("Segoe UI", 8), justify="left").grid(
            row=row[0], column=0, columnspan=2, sticky="w", padx=15, pady=(0, 10))

    def agregar_proveedor():
        abrir_formulario_proveedor(nombre_original=None)

    def editar_proveedor():
        selecciones = tree.selection()
        if not selecciones:
            messagebox.showinfo("Seleccionar", "Seleccione un proveedor primero.")
            return
        nombre = str(tree.item(selecciones[0])["values"][0]).strip()
        custom_names = get_custom_provider_names(config)
        if nombre not in custom_names:
            messagebox.showwarning(
                "No editable aquí",
                "Solo los proveedores creados con 'Agregar proveedor' se pueden editar aquí.\n"
                "Para los proveedores incluidos en la aplicación, usa el botón 'Configurar' "
                "(usuario, contraseña, URL y ubicación en la hoja)."
            )
            return
        abrir_formulario_proveedor(nombre_original=nombre)

    def eliminar_proveedor():
        selecciones = tree.selection()
        if not selecciones:
            messagebox.showinfo("Seleccionar", "Seleccione un proveedor primero.")
            return
        nombre = str(tree.item(selecciones[0])["values"][0]).strip()
        custom_names = get_custom_provider_names(config)
        if nombre not in custom_names:
            messagebox.showwarning(
                "No permitido",
                "Solo se pueden eliminar proveedores creados con 'Agregar proveedor'.\n"
                "Los proveedores incluidos en la aplicación no se pueden borrar desde aquí."
            )
            return
        if not messagebox.askyesno("Confirmar", f"¿Eliminar el proveedor '{nombre}'? Esta acción no se puede deshacer."):
            return

        config["custom_providers"] = [c for c in config.get("custom_providers", []) if c.get("name") != nombre]
        config["enabled_providers"] = [n for n in config.get("enabled_providers", []) if n != nombre]
        config["provider_order"] = [n for n in config.get("provider_order", []) if n != nombre]
        config.get("providers_config", {}).pop(nombre, None)
        save_config(config)
        refresh_tree()

    ttk.Button(btn_panel, text="⚙  Configurar", command=configurar_proveedor, style="Accent.TButton", width=20).pack(pady=4, fill="x")
    ttk.Button(btn_panel, text="↻  Habilitar / Deshab.", command=toggle_proveedor, width=20).pack(pady=4, fill="x")
    ttk.Button(btn_panel, text="👁  Visible / Oculto", command=toggle_visible, width=20).pack(pady=4, fill="x")
    btn_probar_sel = ttk.Button(btn_panel, text="🧪  Probar ahora (visible)", command=probar_ahora_seleccionado, width=20)
    btn_probar_sel.pack(pady=4, fill="x")
    ttk.Button(btn_panel, text="＋  Agregar proveedor", command=agregar_proveedor, width=20).pack(pady=4, fill="x")
    ttk.Button(btn_panel, text="✎  Editar proveedor", command=editar_proveedor, width=20).pack(pady=4, fill="x")
    ttk.Button(btn_panel, text="🗑  Eliminar proveedor", command=eliminar_proveedor, width=20).pack(pady=4, fill="x")
    ttk.Separator(btn_panel, orient="horizontal").pack(fill="x", pady=10)
    ttk.Label(btn_panel, text="Orden:", font=("Segoe UI", 10, "bold")).pack()
    ttk.Button(btn_panel, text="↑  Subir", command=mover_arriba, width=20).pack(pady=2, fill="x")
    ttk.Button(btn_panel, text="↓  Bajar", command=mover_abajo, width=20).pack(pady=2, fill="x")

    # ------------------------------------------------------------------
    # 2. PESTAÑA HORARIOS
    # ------------------------------------------------------------------
    tab_hor = ttk.Frame(notebook)
    notebook.add(tab_hor, text="   Horarios   ")

    hor_frame = ttk.LabelFrame(tab_hor, text="⏰  Configuración de horarios", padding=20)
    hor_frame.pack(fill="both", expand=True, padx=10, pady=10)

    ttk.Label(hor_frame, text="Lunes a Viernes:", font=("Segoe UI", 10, "bold")).grid(row=0, column=0, sticky="w", pady=5)
    entry_lv = ttk.Entry(hor_frame, width=50)
    entry_lv.insert(0, ",".join(config["horarios_lun_vie"]))
    entry_lv.grid(row=0, column=1, padx=15, pady=5)

    ttk.Label(hor_frame, text="Sábados:", font=("Segoe UI", 10, "bold")).grid(row=1, column=0, sticky="w", pady=5)
    entry_sab = ttk.Entry(hor_frame, width=50)
    entry_sab.insert(0, ",".join(config["horarios_sabado"]))
    entry_sab.grid(row=1, column=1, padx=15, pady=5)

    ttk.Label(hor_frame, text="Tolerancia (min):", font=("Segoe UI", 10, "bold")).grid(row=2, column=0, sticky="w", pady=5)
    entry_tol = ttk.Entry(hor_frame, width=10)
    entry_tol.insert(0, str(config["tolerancia_min"]))
    entry_tol.grid(row=2, column=1, padx=15, pady=5, sticky="w")

    def guardar_horarios():
        config["horarios_lun_vie"] = [h.strip() for h in entry_lv.get().split(",") if h.strip()]
        config["horarios_sabado"] = [h.strip() for h in entry_sab.get().split(",") if h.strip()]
        try:
            config["tolerancia_min"] = float(entry_tol.get())
        except ValueError:
            messagebox.showerror("Error", "La tolerancia debe ser un número.")
            return
        save_config(config)
        messagebox.showinfo("Guardado", "Horarios actualizados.")

    ttk.Button(hor_frame, text="💾  Guardar Horarios", command=guardar_horarios, style="Accent.TButton").grid(
        row=3, column=1, pady=20, sticky="e"
    )

    # ------------------------------------------------------------------
    # 3. PESTAÑA CARGAS VOIP (monitor Bitrix24 + OCR)
    # ------------------------------------------------------------------
    tab_cargas = ttk.Frame(notebook)
    notebook.add(tab_cargas, text="   Cargas VOIP   ")

    try:
        import cargas_voip
        cv_cfg = cargas_voip.load_cargas_voip_config()
    except Exception:
        cv_cfg = config.get("cargas_voip_config", {})

    # La pestaña tiene varias secciones y no todas caben sin agrandar la
    # ventana, así que se muestra dentro de un canvas con scrollbar vertical
    # (mismo patrón que el formulario "Agregar proveedor").
    cv_canvas = tk.Canvas(tab_cargas, bg=BG, highlightthickness=0)
    cv_vscroll = ttk.Scrollbar(tab_cargas, orient="vertical", command=cv_canvas.yview)
    cv_content = ttk.Frame(cv_canvas)
    cv_content_window = cv_canvas.create_window((0, 0), window=cv_content, anchor="nw")
    cv_content.bind("<Configure>", lambda e: cv_canvas.configure(scrollregion=cv_canvas.bbox("all")))
    cv_canvas.bind("<Configure>", lambda e: cv_canvas.itemconfigure(cv_content_window, width=e.width))
    cv_canvas.configure(yscrollcommand=cv_vscroll.set)
    cv_canvas.pack(side="left", fill="both", expand=True)
    cv_vscroll.pack(side="right", fill="y")

    def _cv_on_mousewheel(event):
        cv_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def _cv_bind_mousewheel(_event=None):
        cv_canvas.bind_all("<MouseWheel>", _cv_on_mousewheel)

    def _cv_unbind_mousewheel(_event=None):
        cv_canvas.unbind_all("<MouseWheel>")

    cv_canvas.bind("<Enter>", _cv_bind_mousewheel)
    cv_canvas.bind("<Leave>", _cv_unbind_mousewheel)

    conexion_frame = ttk.LabelFrame(cv_content, text="🔌  Conexión Bitrix24", padding=15)
    conexion_frame.pack(fill="x", padx=10, pady=(15, 10))

    ttk.Label(conexion_frame, text="Webhook URL:", font=("Segoe UI", 10, "bold")).grid(row=0, column=0, sticky="w", pady=5)
    cv_webhook_var = tk.StringVar(value=cv_cfg.get("bitrix_webhook_url", ""))
    ttk.Entry(conexion_frame, textvariable=cv_webhook_var, width=55).grid(row=0, column=1, columnspan=3, padx=15, pady=5, sticky="we")

    ttk.Label(conexion_frame, text="Chat ID:", font=("Segoe UI", 10, "bold")).grid(row=1, column=0, sticky="w", pady=5)
    cv_chat_var = tk.StringVar(value=cv_cfg.get("chat_id", "chat135"))
    ttk.Entry(conexion_frame, textvariable=cv_chat_var, width=20).grid(row=1, column=1, padx=15, pady=5, sticky="w")
    conexion_frame.columnconfigure(1, weight=1)

    validacion_frame = ttk.LabelFrame(cv_content, text="✅  Validación", padding=15)
    validacion_frame.pack(fill="x", padx=10, pady=(0, 10))

    ttk.Label(validacion_frame, text="Intervalo de sondeo (s):", font=("Segoe UI", 10, "bold")).grid(row=0, column=0, sticky="w", pady=5)
    cv_poll_var = tk.StringVar(value=str(cv_cfg.get("poll_interval", 20)))
    ttk.Entry(validacion_frame, textvariable=cv_poll_var, width=10).grid(row=0, column=1, padx=15, pady=5, sticky="w")

    ttk.Label(validacion_frame, text="Tolerancia monto USD (%):", font=("Segoe UI", 10, "bold")).grid(row=0, column=2, sticky="w", pady=5, padx=(20, 0))
    cv_tol_var = tk.StringVar(value=str(cv_cfg.get("tolerancia_pct", 0.5)))
    ttk.Entry(validacion_frame, textvariable=cv_tol_var, width=10).grid(row=0, column=3, padx=15, pady=5, sticky="w")

    ttk.Label(validacion_frame, text="Tolerancia TRM (%):", font=("Segoe UI", 10, "bold")).grid(row=1, column=0, sticky="w", pady=5)
    cv_tol_trm_var = tk.StringVar(value=str(cv_cfg.get("tolerancia_trm_pct", 0.1)))
    ttk.Entry(validacion_frame, textvariable=cv_tol_trm_var, width=10).grid(row=1, column=1, padx=15, pady=5, sticky="w")

    ocr_frame = ttk.LabelFrame(cv_content, text="🔎  OCR (Tesseract)", padding=15)
    ocr_frame.pack(fill="x", padx=10, pady=(0, 10))

    def seleccionar_tesseract():
        path = filedialog.askopenfilename(
            title="Seleccionar tesseract.exe",
            filetypes=[("Ejecutable", "*.exe"), ("Todos", "*.*")]
        )
        if path:
            cv_tesseract_var.set(path)
            actualizar_estado_tesseract()

    row_tess = ttk.Frame(ocr_frame)
    row_tess.pack(fill="x")
    ttk.Label(row_tess, text="Ruta tesseract.exe:", font=("Segoe UI", 10, "bold")).pack(side="left", padx=(0, 8))
    cv_tesseract_var = tk.StringVar(value=cv_cfg.get("tesseract_cmd", ""))
    ttk.Entry(row_tess, textvariable=cv_tesseract_var).pack(side="left", expand=True, fill="x", padx=(0, 10))
    ttk.Button(row_tess, text="Examinar", command=seleccionar_tesseract).pack(side="left", padx=3)

    def actualizar_estado_tesseract():
        found = find_tesseract_exe(cv_tesseract_var.get().strip())
        if found:
            cv_tesseract_status_var.set(f"✓  Detectado: {found}")
            if not cv_tesseract_var.get().strip():
                cv_tesseract_var.set(found)
        else:
            cv_tesseract_status_var.set("✗  No se detectó Tesseract OCR en este equipo.")

    def descargar_instalar_tesseract():
        respuesta = messagebox.askyesno(
            "Descargar e instalar Tesseract",
            "Esto descargará el instalador oficial más reciente de Tesseract OCR "
            "desde GitHub (tesseract-ocr/tesseract) y lo ejecutará.\n\n"
            "Windows pedirá permiso de administrador porque se instala en "
            "'Program Files'. Acepta ese aviso para continuar.\n\n¿Continuar?"
        )
        if not respuesta:
            return

        btn_descargar_tesseract.config(state="disabled", text="Instalando...")
        cv_tesseract_status_var.set("Consultando última versión disponible...")

        def progreso(msg):
            root.after(0, lambda: cv_tesseract_status_var.set(msg))

        def tarea():
            try:
                version, exe_path = install_tesseract(progress_cb=progreso)

                def terminar_ok():
                    if exe_path:
                        cv_tesseract_var.set(exe_path)
                        config.setdefault("cargas_voip_config", {})["tesseract_cmd"] = exe_path
                        save_config(config)
                        actualizar_estado_tesseract()
                        messagebox.showinfo(
                            "Tesseract instalado",
                            f"Tesseract {version} instalado correctamente.\nRuta guardada: {exe_path}"
                        )
                    else:
                        actualizar_estado_tesseract()
                        messagebox.showwarning(
                            "Revisar instalación",
                            "Se ejecutó el instalador pero no se pudo confirmar la instalación. "
                            "Si se abrió su asistente, complétalo y vuelve a intentar."
                        )
                root.after(0, terminar_ok)
            except Exception as e:
                root.after(0, lambda: (
                    actualizar_estado_tesseract(),
                    messagebox.showerror("Error", f"No se pudo instalar Tesseract:\n{e}")
                ))
            finally:
                root.after(0, lambda: btn_descargar_tesseract.config(state="normal", text="⬇  Descargar e instalar Tesseract"))

        threading.Thread(target=tarea, daemon=True).start()

    row_tess_estado = ttk.Frame(ocr_frame)
    row_tess_estado.pack(fill="x", pady=(8, 0))
    cv_tesseract_status_var = tk.StringVar(value="Comprobando...")
    ttk.Label(row_tess_estado, textvariable=cv_tesseract_status_var, foreground="#9399b2",
              font=("Segoe UI", 9)).pack(side="left")
    btn_descargar_tesseract = ttk.Button(
        row_tess_estado, text="⬇  Descargar e instalar Tesseract",
        command=descargar_instalar_tesseract, style="Accent.TButton"
    )
    btn_descargar_tesseract.pack(side="right", padx=3)
    actualizar_estado_tesseract()

    aviso_ocr_obligatorio = ("El OCR es obligatorio para validar las capturas de carga: Tesseract debe "
                              "estar instalado (usa el botón de arriba para descargarlo) antes de poder "
                              "iniciar el monitor. El registro detallado de cada lectura queda siempre "
                              "activado en 'cargas_voip.log'.")
    ttk.Label(ocr_frame, text=aviso_ocr_obligatorio, foreground="#9399b2", wraplength=650,
              justify="left", font=("Segoe UI", 9)).pack(anchor="w", pady=(14, 0))

    def guardar_cargas_voip():
        try:
            poll_interval = int(cv_poll_var.get())
        except ValueError:
            messagebox.showerror("Error", "El intervalo de sondeo debe ser un número entero de segundos.")
            return
        try:
            tolerancia_pct = float(cv_tol_var.get())
            tolerancia_trm_pct = float(cv_tol_trm_var.get())
        except ValueError:
            messagebox.showerror("Error", "Las tolerancias deben ser números.")
            return

        config["cargas_voip_config"] = {
            "bitrix_webhook_url": cv_webhook_var.get().strip(),
            "chat_id": cv_chat_var.get().strip() or "chat135",
            "poll_interval": poll_interval,
            "tolerancia_pct": tolerancia_pct,
            "tolerancia_trm_pct": tolerancia_trm_pct,
            "tesseract_cmd": cv_tesseract_var.get().strip(),
            "debug_ocr": True,
        }
        save_config(config)
        messagebox.showinfo("Guardado", "Configuración de Cargas VOIP actualizada.")

    ttk.Button(ocr_frame, text="💾  Guardar configuración", command=guardar_cargas_voip, style="Accent.TButton").pack(
        anchor="e", pady=(15, 0)
    )

    # ---------------- Control del monitor ----------------
    cv_control_frame = ttk.LabelFrame(cv_content, text="▶  Monitor", padding=15)
    cv_control_frame.pack(fill="x", padx=10, pady=(0, 10))

    def cv_check_status():
        nonlocal cargas_voip_process
        if cargas_voip_process is not None:
            if cargas_voip_process.poll() is None:
                cargas_voip_status_var.set("🟢  En ejecución")
                root.after(2000, cv_check_status)
            else:
                cargas_voip_process = None
                cargas_voip_status_var.set("⚫  Detenido")
                btn_cv_iniciar.config(state=tk.NORMAL)
                btn_cv_detener.config(state=tk.DISABLED)

    def cv_iniciar():
        nonlocal cargas_voip_process
        if cargas_voip_process is not None:
            messagebox.showinfo("Cargas VOIP", "El monitor ya está en ejecución.")
            return
        if not config.get("cargas_voip_config", {}).get("bitrix_webhook_url", "").strip():
            messagebox.showerror("Error", "Configura primero la Webhook URL de Bitrix24 y guarda los cambios.")
            return
        if not find_tesseract_exe(config.get("cargas_voip_config", {}).get("tesseract_cmd", "")):
            messagebox.showerror(
                "Tesseract requerido",
                "El OCR es obligatorio para validar las capturas: instala Tesseract con el botón "
                "'⬇  Descargar e instalar Tesseract' antes de iniciar el monitor."
            )
            return

        cargas_voip_process = subprocess.Popen(
            _get_launch_cmd(['--cargas-voip']),
            creationflags=subprocess.CREATE_NO_WINDOW
        )
        cargas_voip_status_var.set("🟢  En ejecución")
        btn_cv_iniciar.config(state=tk.DISABLED)
        btn_cv_detener.config(state=tk.NORMAL)
        messagebox.showinfo("Cargas VOIP", "Monitor iniciado en segundo plano.\nRevisa 'cargas_voip.log' para ver la actividad.")
        root.after(2000, cv_check_status)

    def cv_detener():
        nonlocal cargas_voip_process
        if cargas_voip_process is None:
            return
        try:
            cargas_voip_process.terminate()
            cargas_voip_process.wait(timeout=5)
        except Exception:
            try:
                cargas_voip_process.kill()
            except Exception:
                pass
        cargas_voip_process = None
        cargas_voip_status_var.set("⚫  Detenido")
        btn_cv_iniciar.config(state=tk.NORMAL)
        btn_cv_detener.config(state=tk.DISABLED)
        messagebox.showinfo("Cargas VOIP", "Monitor detenido.")

    def cv_ver_registro():
        csv_path = BASE_DIR / "registro_cargas.csv"
        if not csv_path.exists():
            messagebox.showinfo("Registro", "Todavía no se ha generado 'registro_cargas.csv'.")
            return
        os.startfile(str(csv_path))

    def cv_ver_log():
        log_path = BASE_DIR / "cargas_voip.log"
        if not log_path.exists():
            messagebox.showinfo("Log", "Todavía no se ha generado 'cargas_voip.log'.")
            return
        os.startfile(str(log_path))

    cv_btn_row = ttk.Frame(cv_control_frame)
    cv_btn_row.pack(fill="x")

    btn_cv_iniciar = ttk.Button(cv_btn_row, text="▶  Iniciar Monitor", command=cv_iniciar, style="Accent.TButton")
    btn_cv_iniciar.pack(side="left", padx=3)

    btn_cv_detener = ttk.Button(cv_btn_row, text="⏹  Detener Monitor", command=cv_detener, state="disabled")
    btn_cv_detener.pack(side="left", padx=3)

    ttk.Label(cv_btn_row, textvariable=cargas_voip_status_var, foreground=ACCENT, font=("Segoe UI", 10, "bold")).pack(side="left", padx=15)

    ttk.Button(cv_btn_row, text="📄  Ver registro (CSV)", command=cv_ver_registro).pack(side="right", padx=3)
    ttk.Button(cv_btn_row, text="🗒  Ver log", command=cv_ver_log).pack(side="right", padx=3)

    # ------------------------------------------------------------------
    # 4. PESTAÑA TICKETS (auto-like de tickets asignados)
    # ------------------------------------------------------------------
    tab_tickets = ttk.Frame(notebook)
    notebook.add(tab_tickets, text="   Tickets   ")

    try:
        import auto_like_tickets
        al_cfg = auto_like_tickets.load_auto_like_config()
    except Exception:
        al_cfg = config.get("auto_like_tickets_config", {})

    # Por comodidad, si todavía no se configuró un Webhook propio para
    # Tickets, se sugiere el mismo que ya esté guardado para Cargas VOIP
    # (normalmente es la misma cuenta de Bitrix24).
    if not al_cfg.get("bitrix_webhook_url"):
        al_cfg["bitrix_webhook_url"] = config.get("cargas_voip_config", {}).get("bitrix_webhook_url", "")

    al_conexion_frame = ttk.LabelFrame(tab_tickets, text="🔌  Conexión Bitrix24", padding=15)
    al_conexion_frame.pack(fill="x", padx=10, pady=(15, 10))

    ttk.Label(al_conexion_frame, text="Webhook URL:", font=("Segoe UI", 10, "bold")).grid(row=0, column=0, sticky="w", pady=5)
    al_webhook_var = tk.StringVar(value=al_cfg.get("bitrix_webhook_url", ""))
    ttk.Entry(al_conexion_frame, textvariable=al_webhook_var, width=55).grid(row=0, column=1, columnspan=3, padx=15, pady=5, sticky="we")

    ttk.Label(al_conexion_frame, text="Chat de tickets (ID):", font=("Segoe UI", 10, "bold")).grid(row=1, column=0, sticky="w", pady=5)
    al_chat_var = tk.StringVar(value=al_cfg.get("tickets_chat_id", "chat97"))
    ttk.Entry(al_conexion_frame, textvariable=al_chat_var, width=20).grid(row=1, column=1, padx=15, pady=5, sticky="w")

    ttk.Label(al_conexion_frame, text="Mi User ID:", font=("Segoe UI", 10, "bold")).grid(row=1, column=2, sticky="w", pady=5, padx=(20, 0))
    al_user_var = tk.StringVar(value=str(al_cfg.get("mi_user_id", "13")))
    ttk.Entry(al_conexion_frame, textvariable=al_user_var, width=10).grid(row=1, column=3, padx=15, pady=5, sticky="w")
    al_conexion_frame.columnconfigure(1, weight=1)

    ttk.Label(al_conexion_frame, text="Intervalo de sondeo (s):", font=("Segoe UI", 10, "bold")).grid(row=2, column=0, sticky="w", pady=5)
    al_poll_var = tk.StringVar(value=str(al_cfg.get("poll_interval", 20)))
    ttk.Entry(al_conexion_frame, textvariable=al_poll_var, width=10).grid(row=2, column=1, padx=15, pady=5, sticky="w")

    aviso_tickets = ("Cuando llegue al chat un mensaje con el formato "
                      "\"ticket <número> asignado a [USER=<id>]...\" y el <id> coincida con "
                      "'Mi User ID', la aplicación le da like automáticamente. No reacciona a "
                      "ningún otro tipo de mensaje del chat.")
    ttk.Label(al_conexion_frame, text=aviso_tickets, foreground="#9399b2", wraplength=650,
              justify="left", font=("Segoe UI", 9)).grid(row=3, column=0, columnspan=4, sticky="w", pady=(10, 0))

    def guardar_tickets():
        try:
            poll_interval = int(al_poll_var.get())
        except ValueError:
            messagebox.showerror("Error", "El intervalo de sondeo debe ser un número entero de segundos.")
            return
        if not al_user_var.get().strip():
            messagebox.showerror("Error", "Indica tu User ID de Bitrix24.")
            return

        config["auto_like_tickets_config"] = {
            "bitrix_webhook_url": al_webhook_var.get().strip(),
            "tickets_chat_id": al_chat_var.get().strip() or "chat97",
            "mi_user_id": al_user_var.get().strip(),
            "poll_interval": poll_interval,
        }
        save_config(config)
        messagebox.showinfo("Guardado", "Configuración de Tickets actualizada.")

    ttk.Button(al_conexion_frame, text="💾  Guardar configuración", command=guardar_tickets, style="Accent.TButton").grid(
        row=4, column=3, pady=(15, 0), sticky="e"
    )

    # ---------------- Control del monitor ----------------
    al_control_frame = ttk.LabelFrame(tab_tickets, text="▶  Monitor", padding=15)
    al_control_frame.pack(fill="x", padx=10, pady=(0, 10))

    def al_check_status():
        nonlocal auto_like_process
        if auto_like_process is not None:
            if auto_like_process.poll() is None:
                auto_like_status_var.set("🟢  En ejecución")
                root.after(2000, al_check_status)
            else:
                auto_like_process = None
                auto_like_status_var.set("⚫  Detenido")
                btn_al_iniciar.config(state=tk.NORMAL)
                btn_al_detener.config(state=tk.DISABLED)

    def al_iniciar():
        nonlocal auto_like_process
        if auto_like_process is not None:
            messagebox.showinfo("Tickets", "El monitor ya está en ejecución.")
            return
        if not config.get("auto_like_tickets_config", {}).get("bitrix_webhook_url", "").strip():
            messagebox.showerror("Error", "Configura primero la Webhook URL de Bitrix24 y guarda los cambios.")
            return

        auto_like_process = subprocess.Popen(
            _get_launch_cmd(['--auto-like-tickets']),
            creationflags=subprocess.CREATE_NO_WINDOW
        )
        auto_like_status_var.set("🟢  En ejecución")
        btn_al_iniciar.config(state=tk.DISABLED)
        btn_al_detener.config(state=tk.NORMAL)
        messagebox.showinfo("Tickets", "Monitor iniciado en segundo plano.\nRevisa 'auto_like_tickets.log' para ver la actividad.")
        root.after(2000, al_check_status)

    def al_detener():
        nonlocal auto_like_process
        if auto_like_process is None:
            return
        try:
            auto_like_process.terminate()
            auto_like_process.wait(timeout=5)
        except Exception:
            try:
                auto_like_process.kill()
            except Exception:
                pass
        auto_like_process = None
        auto_like_status_var.set("⚫  Detenido")
        btn_al_iniciar.config(state=tk.NORMAL)
        btn_al_detener.config(state=tk.DISABLED)
        messagebox.showinfo("Tickets", "Monitor detenido.")

    def al_ver_log():
        log_path = BASE_DIR / "auto_like_tickets.log"
        if not log_path.exists():
            messagebox.showinfo("Log", "Todavía no se ha generado 'auto_like_tickets.log'.")
            return
        os.startfile(str(log_path))

    al_btn_row = ttk.Frame(al_control_frame)
    al_btn_row.pack(fill="x")

    btn_al_iniciar = ttk.Button(al_btn_row, text="▶  Iniciar Monitor", command=al_iniciar, style="Accent.TButton")
    btn_al_iniciar.pack(side="left", padx=3)

    btn_al_detener = ttk.Button(al_btn_row, text="⏹  Detener Monitor", command=al_detener, state="disabled")
    btn_al_detener.pack(side="left", padx=3)

    ttk.Label(al_btn_row, textvariable=auto_like_status_var, foreground=ACCENT, font=("Segoe UI", 10, "bold")).pack(side="left", padx=15)

    ttk.Button(al_btn_row, text="🗒  Ver log", command=al_ver_log).pack(side="right", padx=3)

    # ------------------------------------------------------------------
    # 5. PESTAÑA CONFIGURACIÓN (credenciales + URL de Sheets) — siempre
    #    al final, a la derecha de las demás pestañas.
    # ------------------------------------------------------------------
    tab_config = ttk.Frame(notebook)
    notebook.add(tab_config, text="   Configuración   ")

    cred_frame = ttk.LabelFrame(tab_config, text="🔑  Archivo de credenciales", padding=15)
    cred_frame.pack(fill="x", padx=10, pady=(15, 10))

    cred_path_var = tk.StringVar(value=config.get("credentials_path", ""))

    def seleccionar_credenciales():
        path = filedialog.askopenfilename(
            title="Seleccionar credenciales.json",
            filetypes=[("JSON", "*.json"), ("Todos", "*.*")]
        )
        if path:
            cred_path_var.set(path)
            config["credentials_path"] = path
            save_config(config)

    def ayuda_credenciales():
        win = tk.Toplevel(root)
        win.title("Cómo obtener credenciales.json")
        win.geometry("550x500")
        win.configure(bg=BG)
        texto = (
            "1. Ve a: https://console.cloud.google.com/\n"
            "2. Inicia sesión con tu cuenta de Google.\n"
            "3. Arriba a la izquierda, haz clic en 'Seleccionar proyecto' → 'Nuevo proyecto'.\n"
            "4. Ponle un nombre (ej: MiSaldoProyecto) y crea el proyecto.\n\n"
            "ACTIVAR APIs:\n"
            "5. Menú lateral (☰) → 'API y servicios' → 'Biblioteca'.\n"
            "6. Busca 'Google Sheets API' y habilítala.\n"
            "7. Busca 'Google Drive API' y habilítala.\n\n"
            "CREAR CREDENCIALES:\n"
            "8. Menú lateral → 'API y servicios' → 'Credenciales'.\n"
            "9. 'Crear credenciales' → 'Cuenta de servicio'.\n"
            "10. Pon nombre (ej: MiCuentaSheets) y 'Crear y continuar'.\n"
            "11. En rol, elige 'Editor' y continuar. Luego 'Hecho'.\n"
            "12. Haz clic sobre la cuenta de servicio creada.\n"
            "13. Ve a la pestaña 'Claves' → 'Agregar clave' → 'Crear clave nueva' → 'JSON'.\n"
            "14. Se descargará un archivo .json. Renómbralo a 'credenciales.json'.\n\n"
            "Luego, en esta aplicación, selecciona ese archivo con el botón 'Examinar'."
        )
        lbl = ttk.Label(win, text=texto, justify="left", wraplength=500)
        lbl.pack(padx=15, pady=15)
        ttk.Button(win, text="Abrir Google Cloud Console", command=lambda: webbrowser.open("https://console.cloud.google.com/")).pack(pady=5)
        ttk.Button(win, text="Cerrar", command=win.destroy).pack(pady=10)

    row_cred = ttk.Frame(cred_frame)
    row_cred.pack(fill="x")
    ttk.Label(row_cred, text="Ruta:", font=("Segoe UI", 10, "bold")).pack(side="left", padx=(0, 8))
    ttk.Entry(row_cred, textvariable=cred_path_var).pack(side="left", expand=True, fill="x", padx=(0, 10))
    ttk.Button(row_cred, text="Examinar", command=seleccionar_credenciales, style="Accent.TButton").pack(side="left", padx=3)
    ttk.Button(row_cred, text="Ayuda", command=ayuda_credenciales).pack(side="left", padx=3)

    url_frame = ttk.LabelFrame(tab_config, text="🌐  URL de Google Sheets", padding=15)
    url_frame.pack(fill="x", padx=10, pady=(0, 10))

    sheet_url_var = tk.StringVar(value=config.get("google_sheet_url", ""))

    def guardar_url():
        config["google_sheet_url"] = sheet_url_var.get().strip()
        save_config(config)
        messagebox.showinfo("Guardado", "URL de Google Sheets actualizada.")

    row_url = ttk.Frame(url_frame)
    row_url.pack(fill="x")
    ttk.Label(row_url, text="URL:", font=("Segoe UI", 10, "bold")).pack(side="left", padx=(0, 8))
    ttk.Entry(row_url, textvariable=sheet_url_var).pack(side="left", expand=True, fill="x", padx=(0, 10))
    ttk.Button(row_url, text="Guardar URL", command=guardar_url, style="Accent.TButton").pack(side="left", padx=3)

    # ---------------- ChromeDriver / Chrome ----------------
    driver_frame = ttk.LabelFrame(tab_config, text="🧭  Chrome / ChromeDriver", padding=15)
    driver_frame.pack(fill="x", padx=10, pady=(0, 10))

    def _version_label_text():
        v_chrome, v_driver = get_installed_versions()
        v_chrome = v_chrome or "no instalado"
        v_driver = v_driver or "no instalado"
        return f"Chrome: {v_chrome}      ChromeDriver: {v_driver}"

    driver_version_var = tk.StringVar(value=_version_label_text())

    def actualizar_chromedriver():
        respuesta = messagebox.askyesno(
            "Actualizar ChromeDriver",
            "Esto descargará la última versión estable de Chrome portable y "
            "ChromeDriver, reemplazando la instalación actual.\n\n¿Continuar?"
        )
        if not respuesta:
            return

        btn_actualizar_driver.config(state="disabled", text="Actualizando...")
        driver_version_var.set("Consultando última versión disponible...")

        def tarea():
            try:
                version, _, _ = update_chrome_stack()
                root.after(0, lambda: (
                    driver_version_var.set(_version_label_text()),
                    messagebox.showinfo("Actualizado", f"Chrome y ChromeDriver actualizados a la versión {version}.")
                ))
            except Exception as e:
                root.after(0, lambda: (
                    driver_version_var.set(_version_label_text()),
                    messagebox.showerror("Error", f"No se pudo actualizar ChromeDriver:\n{e}")
                ))
            finally:
                root.after(0, lambda: btn_actualizar_driver.config(state="normal", text="Actualizar a la última versión"))

        threading.Thread(target=tarea, daemon=True).start()

    row_driver = ttk.Frame(driver_frame)
    row_driver.pack(fill="x")
    ttk.Label(row_driver, textvariable=driver_version_var).pack(side="left", padx=(0, 10))
    btn_actualizar_driver = ttk.Button(
        row_driver, text="Actualizar a la última versión",
        command=actualizar_chromedriver, style="Accent.TButton"
    )
    btn_actualizar_driver.pack(side="right", padx=3)

    # ====================== BARRA INFERIOR ======================
    bottom = ttk.Frame(root)
    bottom.pack(side="bottom", fill="x", padx=15, pady=(5, 15))

    def check_scheduler_status():
        nonlocal scheduler_process
        if scheduler_process is not None:
            if scheduler_process.poll() is None:
                scheduler_status_var.set("●  En ejecución (esperando horarios)")
                root.after(2000, check_scheduler_status)
            else:
                scheduler_process = None
                scheduler_status_var.set("⚫  Detenido")
                btn_iniciar.config(state=tk.NORMAL)
                btn_detener.config(state=tk.DISABLED)
        else:
            scheduler_status_var.set("⚫  Detenido")

    def iniciar_scheduler():
        nonlocal scheduler_process
        if scheduler_process is not None:
            messagebox.showinfo("Scheduler", "El scheduler ya está en ejecución.")
            return
        if not Path(config.get("credentials_path", "")).exists():
            messagebox.showerror("Error", "Archivo de credenciales no encontrado.")
            return

        scheduler_process = subprocess.Popen(
            _get_launch_cmd(['--scheduler']),
            creationflags=subprocess.CREATE_NO_WINDOW
        )
        scheduler_status_var.set("🟢  En ejecución (esperando horarios)")
        btn_iniciar.config(state=tk.DISABLED)
        btn_detener.config(state=tk.NORMAL)
        messagebox.showinfo("Scheduler", "Scheduler iniciado en segundo plano.\nPuedes seguir usando la interfaz.")
        root.after(2000, check_scheduler_status)

    def detener_scheduler():
        nonlocal scheduler_process
        if scheduler_process is None:
            return
        try:
            scheduler_process.terminate()
            scheduler_process.wait(timeout=5)
        except Exception:
            try:
                scheduler_process.kill()
            except Exception:
                pass
        scheduler_process = None
        scheduler_status_var.set("⚫  Detenido")
        btn_iniciar.config(state=tk.NORMAL)
        btn_detener.config(state=tk.DISABLED)
        messagebox.showinfo("Scheduler", "Scheduler detenido.")

    def ejecutar_ahora():
        if not Path(config.get("credentials_path", "")).exists():
            messagebox.showerror("Error", "Archivo de credenciales no encontrado.")
            return
        subprocess.Popen(
            _get_launch_cmd(['--balance']),
            creationflags=subprocess.CREATE_NEW_CONSOLE
        )

    def on_closing():
        nonlocal scheduler_process, cargas_voip_process, auto_like_process
        if scheduler_process is not None:
            try:
                scheduler_process.terminate()
                scheduler_process.wait(timeout=3)
            except Exception:
                pass
        if cargas_voip_process is not None:
            try:
                cargas_voip_process.terminate()
                cargas_voip_process.wait(timeout=3)
            except Exception:
                pass
        if auto_like_process is not None:
            try:
                auto_like_process.terminate()
                auto_like_process.wait(timeout=3)
            except Exception:
                pass
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_closing)

    control_frame = ttk.Frame(bottom)
    control_frame.pack(side="left")

    btn_iniciar = ttk.Button(control_frame, text="▶  Iniciar Scheduler", command=iniciar_scheduler, style="Accent.TButton")
    btn_iniciar.pack(side="left", padx=3)

    btn_detener = ttk.Button(control_frame, text="⏹  Detener Scheduler", command=detener_scheduler, state="disabled")
    btn_detener.pack(side="left", padx=3)

    scheduler_status_label = ttk.Label(
        control_frame, textvariable=scheduler_status_var,
        foreground=ACCENT, font=("Segoe UI", 10, "bold")
    )
    scheduler_status_label.pack(side="left", padx=15)

    ttk.Button(bottom, text="⚡  Ejecutar Ahora", command=ejecutar_ahora, style="Accent.TButton").pack(side="left", padx=5)
    ttk.Button(bottom, text="Salir", command=on_closing).pack(side="right", padx=5)

    ttk.Label(bottom, text="v2.1 ·by Andres", foreground="#6c7086", font=("Segoe UI", 8)).pack(side="right", padx=10)

    root.mainloop()

# ---------------------------------------------------------------------------
# Punto de entrada
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    if len(sys.argv) > 1:
        if sys.argv[1] == '--scheduler':
            run_scheduler()
        elif sys.argv[1] == '--balance':
            setup_console()
            config = load_config()
            run_balance_cycle(config)
        elif sys.argv[1] == '--cargas-voip':
            import cargas_voip
            cargas_voip.run()
        elif sys.argv[1] == '--auto-like-tickets':
            import auto_like_tickets
            auto_like_tickets.run()
        else:
            print("Argumento desconocido. Use --scheduler, --balance, --cargas-voip o --auto-like-tickets.")
    else:
        run_gui()