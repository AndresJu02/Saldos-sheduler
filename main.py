#!/usr/bin/env python3
"""
Aplicación unificada de saldos.
- Sin argumentos → interfaz gráfica.
- --scheduler     → inicia el planificador en segundo plano.
- --balance       → ejecuta una ronda de consulta de saldos (con consola propia).
"""
from remote_lock import verificar_bloqueo
import os
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
    "provider_order": []
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
                    ok, msg = False, ("Este proveedor todavía no admite modo visible "
                                       "(falta actualizar su get_balance con el parámetro 'headless').")
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
    # 3. PESTAÑA CONFIGURACIÓN (credenciales + URL de Sheets)
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
        nonlocal scheduler_process
        if scheduler_process is not None:
            try:
                scheduler_process.terminate()
                scheduler_process.wait(timeout=3)
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
        else:
            print("Argumento desconocido. Use --scheduler o --balance.")
    else:
        run_gui()