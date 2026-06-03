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
    return providers

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

    root = tk.Tk()
    root.title("Saldos Scheduler")
    root.geometry("835x600")
    root.minsize(750, 500)
    root.configure(bg="#f5f6fa")

    # Centrar ventana
    root.update_idletasks()
    w = root.winfo_width()
    h = root.winfo_height()
    sw = root.winfo_screenwidth()
    sh = root.winfo_screenheight()
    x = (sw - w) // 2
    y = (sh - h) // 2
    root.geometry(f"+{x}+{y}")

    # Estilo moderno
    style = ttk.Style()
    style.theme_use("clam")

    BG = "#f5f6fa"
    FG = "#2c3e50"
    ACCENT = "#3498db"
    ACCENT_DARK = "#2980b9"
    WHITE = "#ffffff"

    style.configure(".", background=BG, foreground=FG, font=("Segoe UI", 10))
    style.configure("TFrame", background=BG)
    style.configure("TLabel", background=BG, foreground=FG)
    style.configure("TLabelframe", background=BG, foreground=FG, borderwidth=0)
    style.configure("TLabelframe.Label", background=BG, foreground=FG, font=("Segoe UI", 11, "bold"))
    style.configure("TNotebook", background=BG, borderwidth=0)
    style.configure("TNotebook.Tab", background="#e0e0e0", foreground=FG, padding=[15, 6], font=("Segoe UI", 10))
    style.map("TNotebook.Tab", background=[("selected", WHITE)], foreground=[("selected", ACCENT)])

    style.configure("Accent.TButton", background=ACCENT, foreground=WHITE, borderwidth=0, padding=[15, 6])
    style.map("Accent.TButton", background=[("active", ACCENT_DARK), ("disabled", "#bdc3c7")])

    style.configure("TButton", background="#ecf0f1", foreground=FG, borderwidth=0, padding=[10, 5])
    style.map("TButton", background=[("active", "#d5dbdb")])

    style.configure("Treeview", background=WHITE, fieldbackground=WHITE, foreground=FG, rowheight=30, borderwidth=0)
    style.configure("Treeview.Heading", background=BG, foreground=FG, font=("Segoe UI", 10, "bold"), borderwidth=0)
    style.map("Treeview", background=[("selected", ACCENT)], foreground=[("selected", WHITE)])

    style.configure("TEntry", fieldbackground=WHITE, borderwidth=1, relief="solid")

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

    columns = ("Proveedor",)
    tree = ttk.Treeview(main_frame, columns=columns, show="tree headings", selectmode="extended")
    tree.heading("#0", text="Estado")
    tree.heading("Proveedor", text="Proveedor")
    tree.column("#0", width=60, anchor="center")
    tree.column("Proveedor", width=250, anchor="w")
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
        for p in providers:
            nombre_limpio = str(p.name).strip()
            icon = "✓" if nombre_limpio in enabled else "✗"
            tree.insert("", "end", text=icon, values=(nombre_limpio,))

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
        win.geometry("400x350")
        win.configure(bg=WHITE)
        win.transient(root)
        win.grab_set()
        entries = {}
        prov_cfg = config.get("providers_config", {}).get(provider.name, {})

        for idx, field in enumerate(provider.config_fields):
            ttk.Label(win, text=field["label"], font=("Segoe UI", 10)).grid(row=idx, column=0, sticky="w", padx=15, pady=8)
            var = tk.StringVar(value=prov_cfg.get(field["key"], field.get("default", "")))
            ttk.Entry(win, textvariable=var, width=30).grid(row=idx, column=1, padx=15, pady=8)
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

    def agregar_proveedor():
        messagebox.showinfo(
            "Agregar proveedor",
            "Para añadir un nuevo proveedor:\n\n"
            "1. Coloca su archivo .py en la carpeta 'providers' junto al ejecutable.\n"
            "2. La clase debe heredar de BaseProvider.\n"
            "3. Reinicia la aplicación.\n\n"
            "Puedes copiar uno existente como plantilla."
        )

    ttk.Button(btn_panel, text="⚙  Configurar", command=configurar_proveedor, style="Accent.TButton", width=20).pack(pady=4, fill="x")
    ttk.Button(btn_panel, text="↻  Habilitar / Deshab.", command=toggle_proveedor, width=20).pack(pady=4, fill="x")
    ttk.Button(btn_panel, text="＋  Agregar proveedor", command=agregar_proveedor, width=20).pack(pady=4, fill="x")
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
        win.configure(bg=WHITE)
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

    ttk.Label(bottom, text="v2.1 ·by Andres", foreground="#95a5a6", font=("Segoe UI", 8)).pack(side="right", padx=10)

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