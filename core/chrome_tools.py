"""
Provisión de Chrome portable + ChromeDriver (descarga, instalación,
actualización, limpieza de procesos huérfanos) y el Selenium driver que
usan los proveedores para leer saldos.
"""
import os
import time
import shutil
import zipfile
import logging
from pathlib import Path

from .paths import BASE_DIR
from .downloads import download_file

logger = logging.getLogger("main")

CHROME_DIR = BASE_DIR / 'chrome-portable'
CHROMEDRIVER_DIR = BASE_DIR / 'chromedriver-portable'

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


def get_robust_driver(chrome_exe: str, chromedriver_exe: str, headless: bool = True, user_data_dir: str = None):
    opts = ChromeOptions()
    opts.binary_location = chrome_exe
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_argument("--start-maximized")
    opts.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36")
    opts.add_argument("--disable-extensions")
    opts.add_argument("--disable-infobars")
    opts.add_argument("--no-default-browser-check")
    opts.add_argument("--no-first-run")
    if user_data_dir:
        # Perfil de Chrome PERSISTENTE (cookies/sesión sobreviven entre
        # corridas) en vez del perfil descartable de siempre -algunos
        # sitios (ej. IDT) tratan un navegador sin cookies como "dispositivo
        # desconocido" y piden verificación extra; con un perfil que se
        # reutiliza, después de la primera vez queda reconocido igual que
        # el navegador normal de un usuario real.
        opts.add_argument(f"--user-data-dir={user_data_dir}")
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
