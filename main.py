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
    sys.stdin = open('CONIN$', 'r')          # ← permite usar input()

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

CHROME_VERSION = "148.0.7778.217"
CHROME_URL = f"https://storage.googleapis.com/chrome-for-testing-public/{CHROME_VERSION}/win64/chrome-win64.zip"
CHROMEDRIVER_URL = f"https://storage.googleapis.com/chrome-for-testing-public/{CHROME_VERSION}/win64/chromedriver-win64.zip"

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

def ensure_chrome():
    chrome_exe = CHROME_DIR / 'chrome-win64' / 'chrome.exe'
    if chrome_exe.exists():
        return str(chrome_exe)
    logger.info("Instalando Chrome portable...")
    zip_path = BASE_DIR / 'chrome.zip'
    download_file(CHROME_URL, zip_path)
    shutil.rmtree(CHROME_DIR, ignore_errors=True)
    CHROME_DIR.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(CHROME_DIR)
    zip_path.unlink()
    logger.info("Chrome instalado.")
    return str(chrome_exe)

def ensure_chromedriver():
    driver_exe = CHROMEDRIVER_DIR / 'chromedriver.exe'
    if driver_exe.exists():
        return str(driver_exe)

    logger.info("Instalando ChromeDriver...")
    zip_path = BASE_DIR / 'chromedriver.zip'
    if zip_path.exists():
        zip_path.unlink()
    if CHROMEDRIVER_DIR.exists():
        shutil.rmtree(CHROMEDRIVER_DIR, ignore_errors=True)

    download_file(CHROMEDRIVER_URL, zip_path)

    temp_extract = CHROMEDRIVER_DIR.parent / 'chromedriver_temp'
    shutil.rmtree(temp_extract, ignore_errors=True)
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

    shutil.rmtree(temp_extract, ignore_errors=True)
    zip_path.unlink()
    logger.info("ChromeDriver instalado correctamente.")
    return str(driver_exe)

def cleanup_orphans():
    """Mata procesos de chrome (solo de la carpeta portable) y chromedriver que estén colgados."""
    try:
        import psutil
    except ImportError:
        return

    current_pid = os.getpid()
    # Ruta de la carpeta que contiene nuestro Chrome portable
    chrome_portable_dir = str(CHROME_DIR.resolve()).lower()  # ej: C:\...\chrome-portable

    for proc in psutil.process_iter(['pid', 'name', 'exe']):
        try:
            name = (proc.info['name'] or '').lower()
            exe_path = (proc.info['exe'] or '').lower()

            # Eliminar chromedriver (suele quedar huérfano)
            if 'chromedriver' in name:
                if proc.info['pid'] != current_pid:
                    proc.kill()
                    logger.info(f"Limpieza: chromedriver (PID {proc.info['pid']}) terminado.")
            # Eliminar chrome.exe solo si está dentro de la carpeta chrome-portable
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
    # -------------------- Opciones exactas del script original --------------------
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
    # Bypass HTTP inseguro
    opts.add_argument("--allow-running-insecure-content")
    opts.add_argument("--ignore-certificate-errors")
    opts.add_argument("--allow-insecure-localhost")
    opts.add_argument("--unsafely-treat-insecure-origin-as-secure=http://178.105.24.84,http://158.69.177.101,http://clientes.datavoice.com.co,http://45.226.115.82")
    # ----------------------------------------------------------------------------

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
    return providers

# Asegurar que el módulo compartido de proveedores se cargue
import providers.vos_helpers

# ---------------------------------------------------------------------------
# Orden de proveedores
# ---------------------------------------------------------------------------
def sort_providers_by_order(providers: List, config: dict) -> List:
    order = config.get("provider_order", [])
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
# Ciclo de consulta de saldos (con salida a terminal)
# ---------------------------------------------------------------------------
def run_balance_cycle(config: dict):
    # Configurar salida UTF-8
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

    cred_path = Path(config['credentials_path'])
    if not cred_path.exists():
        print(f"ERROR: Credenciales no encontradas en {cred_path}", flush=True)
        return

    # Limpiar procesos huérfanos ANTES de crear nuevos drivers
    cleanup_orphans()

    # Verificar bloqueo remoto
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

        print(f"\nProcesando {provider.name}...", flush=True)
        try:
            success, msg = provider.get_balance(
                final_cfg, sh, config['google_sheet_url'],
                driver_paths={"chrome_exe": chrome_exe, "chromedriver_exe": chromedriver_exe},
                get_driver_fn=get_robust_driver
            )
            if success:
                print(f"  ✅ {provider.name} -> OK ({msg})", flush=True)
            else:
                print(f"  ❌ {provider.name} -> FALLO: {msg}", flush=True)
        except Exception as e:
            print(f"  ❌ {provider.name} -> EXCEPCIÓN: {e}", flush=True)
            traceback.print_exc()

    print("\n" + "=" * 50, flush=True)
    print("  TODAS LAS TAREAS COMPLETADAS", flush=True)
    print("=" * 50, flush=True)

    try:
        sh.update_cell(5, 7, datetime.now().strftime("%H:%M:%S"))
    except Exception:
        pass

    # Pausa hasta que el usuario cierre la ventana
    if getattr(sys, 'frozen', False):
        print("\nProceso finalizado. Puede cerrar esta ventana.", flush=True)
        try:
            input()   # Espera un Enter, pero la X también cierra
        except (EOFError, OSError):
            pass

# ---------------------------------------------------------------------------
# Planificador (scheduler)
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
    # Control de horarios ya ejecutados hoy
    executed_today = set()
    current_date = datetime.now().date()

    logger.info("Scheduler iniciado.")
    while True:
        ahora = datetime.now()
        # Reiniciar el registro diario si cambió el día
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
                # Verificar tolerancia
                if abs((ahora - target).total_seconds()) > config['tolerancia_min'] * 60:
                    continue
                # Si ya se ejecutó hoy, ignorar
                if hhmm in executed_today:
                    continue

                # Lock opcional en Sheets
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
                # Marcar como ejecutado hoy
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

    root = tk.Tk()
    root.title("Saldos Scheduler")
    root.geometry("750x550")

    scheduler_process = None
    scheduler_status_var = tk.StringVar(value="●  Detenido")
    config = load_config()
    
# ------------------------------------------------------------
    # NUEVO: Verificar bloqueo remoto al iniciar la interfaz
    # ------------------------------------------------------------
    from remote_lock import verificar_bloqueo
    if verificar_bloqueo():
        messagebox.showwarning(
            "Aplicación bloqueada",
            "⚠️  Esta aplicación ha sido bloqueada por el administrador.\n\n"
            "Contacte al soporte para más información."
        )
    # ------------------------------------------------------------

    notebook = ttk.Notebook(root)
    notebook.pack(fill='both', expand=True, padx=10, pady=10)
    notebook = ttk.Notebook(root)
    notebook.pack(fill='both', expand=True, padx=10, pady=10)

    # ---------- Pestaña Proveedores ----------
    tab_prov = ttk.Frame(notebook)
    notebook.add(tab_prov, text="Proveedores")

    cred_frame = ttk.LabelFrame(tab_prov, text="Archivo de credenciales (credenciales.json)")
    cred_frame.pack(fill=tk.X, padx=5, pady=5)

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
        lbl = ttk.Label(win, text=texto, justify=tk.LEFT, wraplength=500)
        lbl.pack(padx=10, pady=10)

        def abrir_consola():
            webbrowser.open("https://console.cloud.google.com/")
        ttk.Button(win, text="Abrir Google Cloud Console", command=abrir_consola).pack(pady=5)
        ttk.Button(win, text="Cerrar", command=win.destroy).pack(pady=5)

    row_frame = ttk.Frame(cred_frame)
    row_frame.pack(fill=tk.X, padx=5, pady=5)
    ttk.Label(row_frame, text="Ruta:").pack(side=tk.LEFT)
    entry_cred = ttk.Entry(row_frame, textvariable=cred_path_var, width=50)
    entry_cred.pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
    ttk.Button(row_frame, text="Examinar", command=seleccionar_credenciales).pack(side=tk.LEFT, padx=2)
    ttk.Button(row_frame, text="Ayuda", command=ayuda_credenciales).pack(side=tk.LEFT, padx=2)

    listbox = tk.Listbox(tab_prov, selectmode=tk.SINGLE, width=40)
    listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 10))

    btn_frame = ttk.Frame(tab_prov)
    btn_frame.pack(side=tk.RIGHT, fill=tk.Y)

    def refresh_list():
        listbox.delete(0, tk.END)
        providers = get_all_providers()
        providers = sort_providers_by_order(providers, config)
        enabled = config.get("enabled_providers", [])
        for p in providers:
            mark = "✓" if p.name in enabled else "✗"
            listbox.insert(tk.END, f"{mark} {p.name}")

    refresh_list()

    def configurar_proveedor():
        sel = listbox.curselection()
        if not sel:
            return
        providers = get_all_providers()
        providers = sort_providers_by_order(providers, config)
        provider = providers[sel[0]]

        win = tk.Toplevel(root)
        win.title(f"Configurar {provider.name}")
        entries = {}
        row = 0
        prov_cfg = config.get("providers_config", {}).get(provider.name, {})
        for field in provider.config_fields:
            ttk.Label(win, text=field["label"]).grid(row=row, column=0, sticky='w', padx=5, pady=2)
            var = tk.StringVar(value=prov_cfg.get(field["key"], field.get("default", "")))
            ttk.Entry(win, textvariable=var, width=30).grid(row=row, column=1, padx=5, pady=2)
            entries[field["key"]] = var
            row += 1

        def guardar():
            new_cfg = {k: v.get() for k, v in entries.items()}
            config.setdefault("providers_config", {})[provider.name] = new_cfg
            save_config(config)
            win.destroy()

        ttk.Button(win, text="Guardar", command=guardar).grid(row=row, column=1, pady=10, sticky='e')

    def toggle_proveedor():
        sel = listbox.curselection()
        if not sel:
            return
        providers = get_all_providers()
        providers = sort_providers_by_order(providers, config)
        name = providers[sel[0]].name
        enabled = config.get("enabled_providers", [])
        if name in enabled:
            enabled.remove(name)
        else:
            enabled.append(name)
        config["enabled_providers"] = enabled
        save_config(config)
        refresh_list()

    def agregar_proveedor():
        messagebox.showinfo(
            "Agregar proveedor",
            "Para añadir un nuevo proveedor:\n\n"
            "1. Coloca su archivo .py en la carpeta 'providers' junto al ejecutable.\n"
            "2. La clase debe heredar de BaseProvider.\n"
            "3. Reinicia la aplicación.\n\n"
            "Puedes copiar uno existente como plantilla."
        )

    def mover_arriba():
        sel = listbox.curselection()
        if not sel or sel[0] == 0:
            return
        idx = sel[0]
        order = config.get("provider_order", [])
        if idx >= len(order):
            return
        order[idx], order[idx-1] = order[idx-1], order[idx]
        config["provider_order"] = order
        save_config(config)
        refresh_list()
        listbox.selection_set(idx-1)

    def mover_abajo():
        sel = listbox.curselection()
        if not sel:
            return
        idx = sel[0]
        order = config.get("provider_order", [])
        if idx >= len(order) - 1:
            return
        order[idx], order[idx+1] = order[idx+1], order[idx]
        config["provider_order"] = order
        save_config(config)
        refresh_list()
        listbox.selection_set(idx+1)

    ttk.Button(btn_frame, text="Configurar", command=configurar_proveedor).pack(pady=5, fill=tk.X)
    ttk.Button(btn_frame, text="Habilitar/Deshab.", command=toggle_proveedor).pack(pady=5, fill=tk.X)
    ttk.Button(btn_frame, text="Agregar proveedor", command=agregar_proveedor).pack(pady=5, fill=tk.X)
    ttk.Separator(btn_frame, orient='horizontal').pack(pady=10, fill=tk.X)
    ttk.Label(btn_frame, text="Orden:").pack()
    ttk.Button(btn_frame, text="Subir ↑", command=mover_arriba).pack(pady=2, fill=tk.X)
    ttk.Button(btn_frame, text="Bajar ↓", command=mover_abajo).pack(pady=2, fill=tk.X)

    # ---------- Pestaña Horarios ----------
    tab_hor = ttk.Frame(notebook)
    notebook.add(tab_hor, text="Horarios")

    ttk.Label(tab_hor, text="Lunes a Viernes (separados por coma):").grid(row=0, column=0, sticky='w', padx=5, pady=5)
    entry_lv = ttk.Entry(tab_hor, width=50)
    entry_lv.insert(0, ",".join(config['horarios_lun_vie']))
    entry_lv.grid(row=0, column=1, padx=5, pady=5)

    ttk.Label(tab_hor, text="Sábados:").grid(row=1, column=0, sticky='w', padx=5, pady=5)
    entry_sab = ttk.Entry(tab_hor, width=50)
    entry_sab.insert(0, ",".join(config['horarios_sabado']))
    entry_sab.grid(row=1, column=1, padx=5, pady=5)

    ttk.Label(tab_hor, text="Tolerancia (min):").grid(row=2, column=0, sticky='w', padx=5, pady=5)
    entry_tol = ttk.Entry(tab_hor, width=10)
    entry_tol.insert(0, str(config['tolerancia_min']))
    entry_tol.grid(row=2, column=1, sticky='w', padx=5, pady=5)

    def guardar_horarios():
        config['horarios_lun_vie'] = [h.strip() for h in entry_lv.get().split(',') if h.strip()]
        config['horarios_sabado'] = [h.strip() for h in entry_sab.get().split(',') if h.strip()]
        try:
            config['tolerancia_min'] = float(entry_tol.get())
        except ValueError:
            messagebox.showerror("Error", "La tolerancia debe ser un número.")
            return
        save_config(config)
        messagebox.showinfo("Guardado", "Horarios actualizados.")

    ttk.Button(tab_hor, text="Guardar Horarios", command=guardar_horarios).grid(row=3, column=1, pady=10, sticky='e')

    # ---------- Botones inferiores ----------
    bottom = ttk.Frame(root)
    bottom.pack(side=tk.BOTTOM, fill=tk.X, padx=10, pady=10)

    def check_scheduler_status():
        nonlocal scheduler_process
        if scheduler_process is not None:
            if scheduler_process.poll() is None:
                scheduler_status_var.set("●  En ejecución (esperando horarios)")
                root.after(2000, check_scheduler_status)
            else:
                scheduler_process = None
                scheduler_status_var.set("●  Detenido")
                btn_iniciar.config(state=tk.NORMAL)
                btn_detener.config(state=tk.DISABLED)
        else:
            scheduler_status_var.set("●  Detenido")

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
        scheduler_status_var.set("●  En ejecución (esperando horarios)")
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
        scheduler_status_var.set("●  Detenido")
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
    control_frame.pack(side=tk.LEFT, padx=5)

    btn_iniciar = ttk.Button(control_frame, text="Iniciar Scheduler", command=iniciar_scheduler)
    btn_iniciar.pack(side=tk.LEFT, padx=2)

    btn_detener = ttk.Button(control_frame, text="Detener Scheduler", command=detener_scheduler, state=tk.DISABLED)
    btn_detener.pack(side=tk.LEFT, padx=2)

    scheduler_status_label = ttk.Label(control_frame, textvariable=scheduler_status_var, foreground="gray")
    scheduler_status_label.pack(side=tk.LEFT, padx=10)

    ttk.Button(bottom, text="Ejecutar Ahora", command=ejecutar_ahora).pack(side=tk.LEFT, padx=5)
    ttk.Button(bottom, text="Salir", command=on_closing).pack(side=tk.RIGHT, padx=5)

    root.mainloop()

# ---------------------------------------------------------------------------
# Punto de entrada
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    if len(sys.argv) > 1:
        if sys.argv[1] == '--scheduler':
            run_scheduler()
        elif sys.argv[1] == '--balance':
            setup_console()          # Activa la consola para ver la salida
            config = load_config()
            run_balance_cycle(config)
        else:
            print("Argumento desconocido. Use --scheduler o --balance.")
    else:
        run_gui()