"""
Provisión de Tesseract OCR: consulta la última versión publicada en
GitHub, la descarga e instala. Lo usa el monitor de Cargas VOIP.
"""
import re
import subprocess
import shutil
import logging
from pathlib import Path

from .paths import BASE_DIR
from .downloads import download_file

logger = logging.getLogger("main")

TESSERACT_RELEASES_API = "https://api.github.com/repos/tesseract-ocr/tesseract/releases/latest"
TESSERACT_ASSET_RE = re.compile(r"^tesseract-ocr-w64-setup-.*\.exe$", re.IGNORECASE)

# Versión "de respaldo" usada únicamente si la consulta a la API de GitHub
# fallara (sin internet, límite de peticiones, cambio de nombre del asset).
TESSERACT_FALLBACK_VERSION = "5.5.3"
TESSERACT_FALLBACK_URL = "https://github.com/tesseract-ocr/tesseract/releases/download/5.5.3/tesseract-ocr-w64-setup-5.5.3.20260724.exe"

# El instalador oficial de Windows no siempre trae el paquete de español
# (cargas_voip.py llama a Tesseract con lang="spa+eng"; sin este archivo,
# Tesseract ignora "spa" en silencio y sigue solo con inglés). Se descarga
# del mismo repo oficial (tessdata_fast) que trae el eng.traineddata que
# ya instala el setup.
SPA_TRAINEDDATA_URL = "https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/main/spa.traineddata"


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


def has_spanish_traineddata(exe_path: str) -> bool:
    """True si junto al tesseract.exe indicado ya existe tessdata/spa.traineddata."""
    if not exe_path:
        return False
    return (Path(exe_path).parent / "tessdata" / "spa.traineddata").is_file()


def _elevated_copy(src: Path, dst: Path, wait_seconds: int = 90) -> bool:
    """
    Copia src->dst solicitando elevación (UAC) vía ShellExecute "runas",
    ya que dst suele estar en Program Files. Es asíncrono (Windows no
    permite esperar el proceso elevado de forma directa desde acá), así
    que sondea la aparición del archivo hasta wait_seconds.
    """
    import ctypes
    import time

    params = f'/c copy /Y "{src}" "{dst}"'
    ret = ctypes.windll.shell32.ShellExecuteW(None, "runas", "cmd.exe", params, None, 0)
    if ret <= 32:
        return False  # el usuario canceló el aviso de UAC, o falló al lanzarlo

    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        if dst.is_file():
            return True
        time.sleep(1)
    return dst.is_file()


def ensure_spanish_traineddata(exe_path: str, progress_cb=None) -> bool:
    """
    Se asegura de que exista tessdata/spa.traineddata junto al tesseract.exe
    indicado; si falta, lo descarga y lo copia (pidiendo permisos de
    administrador con UAC si la carpeta tessdata no es escribible, como
    ocurre en la instalación por defecto dentro de Program Files).
    """
    def log(msg):
        logger.info(msg)
        if progress_cb:
            progress_cb(msg)

    if not exe_path:
        return False
    dest = Path(exe_path).parent / "tessdata" / "spa.traineddata"
    if dest.is_file():
        return True

    log("Descargando paquete de idioma español (spa.traineddata)...")
    tmp_path = BASE_DIR / "spa.traineddata"
    download_file(SPA_TRAINEDDATA_URL, tmp_path)

    try:
        try:
            shutil.copy2(tmp_path, dest)
        except PermissionError:
            log("Se requieren permisos de administrador para copiar a "
                f"'{dest.parent}'; solicitando elevación (UAC)...")
            if not _elevated_copy(tmp_path, dest):
                log("No se pudo instalar el paquete de español (se canceló "
                    "el aviso de permisos, o falló la copia). Tesseract "
                    "seguirá reconociendo solo en inglés.")
                return False
    finally:
        try:
            tmp_path.unlink()
        except Exception:
            pass

    ok = dest.is_file()
    log("Paquete de español instalado correctamente." if ok else
        "No se pudo confirmar la instalación del paquete de español.")
    return ok


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
        ensure_spanish_traineddata(exe_path, progress_cb=progress_cb)
    else:
        log("El instalador se ejecutó pero no se encontró tesseract.exe; "
            "si se abrió el asistente, complétalo y vuelve a intentar.")
    return version, exe_path
