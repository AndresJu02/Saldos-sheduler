"""
Provisión de Tesseract OCR: consulta la última versión publicada en
GitHub, la descarga e instala. Lo usa el monitor de Cargas VOIP.
"""
import os
import re
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


def _fallback_tessdata_dir() -> Path:
    """
    Carpeta de tessdata alternativa, en el perfil del usuario (no requiere
    permisos de administrador). Se usa cuando no se puede copiar
    spa.traineddata a la carpeta oficial dentro de 'Program Files', como
    ocurre si la cuenta de Windows de ese equipo no es administradora y
    cancela (o no puede completar) el aviso de permisos (UAC).
    """
    base = os.environ.get("LOCALAPPDATA")
    return (Path(base) if base else BASE_DIR) / "SaldosScheduler" / "tessdata"


def find_tessdata_dir(exe_path: str):
    """
    Devuelve la carpeta tessdata a pasarle a Tesseract vía '--tessdata-dir'
    cuando el español NO está en la carpeta oficial junto al exe (porque se
    instaló en la alternativa de _fallback_tessdata_dir). Devuelve None si
    la carpeta oficial ya sirve tal cual (comportamiento por defecto de
    Tesseract, sin necesidad de este flag) o si no hay español en ningún lado.
    """
    if not exe_path:
        return None
    if (Path(exe_path).parent / "tessdata" / "spa.traineddata").is_file():
        return None
    fallback = _fallback_tessdata_dir()
    if (fallback / "spa.traineddata").is_file() and (fallback / "eng.traineddata").is_file():
        return fallback
    return None


def has_spanish_traineddata(exe_path: str) -> bool:
    """True si Tesseract puede leer español: spa.traineddata junto al exe
    (instalación oficial), o en la carpeta alternativa (ver find_tessdata_dir)."""
    if not exe_path:
        return False
    if (Path(exe_path).parent / "tessdata" / "spa.traineddata").is_file():
        return True
    fallback = _fallback_tessdata_dir()
    return (fallback / "spa.traineddata").is_file() and (fallback / "eng.traineddata").is_file()


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


def _run_elevated(exe_path: Path, params: str, wait_seconds: int = 300) -> bool:
    """
    Ejecuta exe_path pidiendo elevación (UAC) vía ShellExecute "runas", y
    ESPERA a que termine.

    Esto es necesario (y no un simple subprocess.run) porque el instalador
    oficial de Tesseract exige administrador en su propio manifiesto
    (así instala Inno Setup cuando el destino es 'Program Files'). Windows
    solo ofrece elevar un programa así cuando se lanza vía ShellExecute,
    que es lo que hace Explorer al doble clic; lanzado con
    subprocess.run/CreateProcess (como hacía antes este código), Windows lo
    rechaza al instante con "WinError 740: se requiere elevación", sin
    mostrar ningún aviso ni instalar nada; por eso el botón nunca llegaba a
    instalar el paquete de español (se detenía aquí, en silencio).

    Devuelve True si el proceso se lanzó y terminó (sin importar su código
    de salida); False si el usuario canceló el aviso de UAC, si falló al
    lanzarlo, o si no terminó dentro de wait_seconds.
    """
    import ctypes
    from ctypes import wintypes

    class SHELLEXECUTEINFOW(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("fMask", ctypes.c_ulong),
            ("hwnd", wintypes.HWND),
            ("lpVerb", wintypes.LPCWSTR),
            ("lpFile", wintypes.LPCWSTR),
            ("lpParameters", wintypes.LPCWSTR),
            ("lpDirectory", wintypes.LPCWSTR),
            ("nShow", ctypes.c_int),
            ("hInstApp", wintypes.HINSTANCE),
            ("lpIDList", ctypes.c_void_p),
            ("lpClass", wintypes.LPCWSTR),
            ("hkeyClass", wintypes.HKEY),
            ("dwHotKey", wintypes.DWORD),
            ("hIcon", wintypes.HANDLE),
            ("hProcess", wintypes.HANDLE),
        ]

    SEE_MASK_NOCLOSEPROCESS = 0x00000040
    SW_SHOWNORMAL = 1

    sei = SHELLEXECUTEINFOW()
    sei.cbSize = ctypes.sizeof(SHELLEXECUTEINFOW)
    sei.fMask = SEE_MASK_NOCLOSEPROCESS
    sei.lpVerb = "runas"
    sei.lpFile = str(exe_path)
    sei.lpParameters = params
    sei.nShow = SW_SHOWNORMAL

    if not ctypes.windll.shell32.ShellExecuteExW(ctypes.byref(sei)):
        return False  # el usuario canceló el aviso de UAC, o falló al lanzarlo
    if not sei.hProcess:
        return True  # se lanzó pero no dio handle para esperar (poco común)

    ctypes.windll.kernel32.WaitForSingleObject(sei.hProcess, wait_seconds * 1000)
    ctypes.windll.kernel32.CloseHandle(sei.hProcess)
    return True


def ensure_spanish_traineddata(exe_path: str, progress_cb=None) -> bool:
    """
    Se asegura de que Tesseract pueda leer español: si falta
    spa.traineddata, lo descarga y lo instala, primero intentando la
    carpeta oficial junto al tesseract.exe indicado (pidiendo permisos de
    administrador con UAC si no es escribible, como ocurre en la
    instalación por defecto dentro de Program Files). Si esa elevación no
    se puede completar -por ejemplo porque la cuenta de Windows de ese
    equipo no es administradora-, instala en una carpeta alternativa en el
    perfil del usuario que no requiere privilegios especiales (ver
    find_tessdata_dir(); quien llame a Tesseract debe pasarle esa carpeta
    con '--tessdata-dir' cuando aplique).
    """
    def log(msg):
        logger.info(msg)
        if progress_cb:
            progress_cb(msg)

    if not exe_path:
        return False
    official_dir = Path(exe_path).parent / "tessdata"
    fallback_dir = _fallback_tessdata_dir()
    if (official_dir / "spa.traineddata").is_file():
        return True
    if (fallback_dir / "spa.traineddata").is_file() and (fallback_dir / "eng.traineddata").is_file():
        return True

    log("Descargando paquete de idioma español (spa.traineddata)...")
    tmp_path = BASE_DIR / "spa.traineddata"
    try:
        download_file(SPA_TRAINEDDATA_URL, tmp_path)
    except Exception as e:
        log(f"No se pudo descargar el paquete de español ({e}). Revisa la "
            "conexión a internet de este equipo, o si el firewall/antivirus "
            "está bloqueando 'raw.githubusercontent.com'.")
        return False

    try:
        dest = official_dir / "spa.traineddata"
        try:
            shutil.copy2(tmp_path, dest)
            log("Paquete de español instalado correctamente.")
            return dest.is_file()
        except PermissionError:
            log("Se requieren permisos de administrador para copiar a "
                f"'{official_dir}'; solicitando elevación (UAC)...")
            if _elevated_copy(tmp_path, dest) and dest.is_file():
                log("Paquete de español instalado correctamente.")
                return True
            log("No se pudo instalar el paquete de español en la carpeta "
                "oficial (se canceló el aviso de permisos, o esta cuenta de "
                "Windows no es administradora). Se instalará en una carpeta "
                "alternativa que no requiere permisos especiales.")

        # Alternativa sin privilegios: copia spa + eng.traineddata (el que
        # ya trae el instalador oficial) a una carpeta propia del usuario,
        # ya que al usar '--tessdata-dir' Tesseract deja de buscar en la
        # carpeta oficial para TODos los idiomas, no solo el que faltaba.
        try:
            fallback_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(tmp_path, fallback_dir / "spa.traineddata")
            eng_src = official_dir / "eng.traineddata"
            if eng_src.is_file():
                shutil.copy2(eng_src, fallback_dir / "eng.traineddata")
            ok = (fallback_dir / "spa.traineddata").is_file() and (fallback_dir / "eng.traineddata").is_file()
            log("Paquete de español instalado correctamente (carpeta alternativa)." if ok else
                "No se pudo instalar el paquete de español en la carpeta alternativa.")
            return ok
        except Exception as e:
            log(f"No se pudo instalar el paquete de español: {e}")
            return False
    finally:
        try:
            tmp_path.unlink()
        except Exception:
            pass


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
    params = (
        '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP- '
        r'/DIR="C:\Program Files\Tesseract-OCR"'
    )
    try:
        if not _run_elevated(installer_path, params):
            log("No se pudo iniciar el instalador (se canceló el aviso de "
                "permisos de administrador, o esta cuenta de Windows no "
                "puede elevar).")
    finally:
        try:
            installer_path.unlink()
        except Exception:
            pass

    exe_path = find_tesseract_exe()
    if exe_path:
        log(f"Tesseract {version} instalado en: {exe_path}")
        try:
            ensure_spanish_traineddata(exe_path, progress_cb=progress_cb)
        except Exception as e:
            # No dejar que un fallo instalando el paquete de español (sin
            # internet, permisos, etc.) se reporte como si Tesseract mismo
            # no se hubiera podido instalar: el exe ya quedó instalado.
            log(f"Tesseract se instaló correctamente, pero no se pudo instalar "
                f"el paquete de español ({e}). Puedes reintentarlo con el mismo botón.")
    else:
        log("El instalador se ejecutó pero no se encontró tesseract.exe; "
            "si se abrió el asistente, complétalo y vuelve a intentar.")
    return version, exe_path
