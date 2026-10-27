#!/usr/bin/env python3
"""
Lanzador de Saldos Scheduler.

Este es el único archivo que hace falta copiar a una PC nueva. Cada vez
que se abre, consulta la última release publicada en GitHub
(releases/latest, sin necesidad de tocar este archivo en cada
actualización) y:

  - Si todavía no está instalado en esta PC, lo descarga e instala en
    %LOCALAPPDATA%\\SaldosScheduler\\app.
  - Si ya está instalado pero hay una versión más nueva publicada, se
    actualiza solo antes de abrir.
  - Si ya está instalado, está al día, o GitHub no responde a tiempo
    (sin internet, etc.), abre directo lo que ya haya instalado -nunca
    se bloquea al usuario por no poder consultar GitHub-.

No se puede lograr esto empaquetando el programa real DENTRO de este
mismo lanzador (como si fuera un .exe de un solo archivo con todo
adentro): PyInstaller descomprime ese contenido entero en CADA arranque,
sin excepción -esa es la razón por la que el .exe original de un solo
archivo tardaba varios segundos en abrir-. Por eso el programa real se
descarga una sola vez (o cuando cambia de versión) y se deja instalado
aparte, en vez de ir pegado a este lanzador.

Para publicar una versión nueva del programa: subir un .zip actualizado
(SaldosScheduler.exe + _internal, sin archivos de configuración propios
de cada PC) como asset de un Release NUEVO en GitHub (con un tag
distinto, ej. "app-v2"). No hace falta tocar este archivo ni
recompilarlo: como se consulta "releases/latest", el lanzador detecta
la versión nueva solo, en cada PC, la próxima vez que se abra.
"""
import os
import sys
import json
import ctypes
import zipfile
import tempfile
import subprocess
import urllib.error
import urllib.request

APP_NAME = "SaldosScheduler"
REPO = "AndresJu02/Saldos-sheduler"
RELEASES_API = f"https://api.github.com/repos/{REPO}/releases/latest"
CHECK_TIMEOUT = 3  # segundos: si GitHub no responde rápido, se abre lo que ya esté instalado

INSTALL_DIR = os.path.join(
    os.environ.get("LOCALAPPDATA") or tempfile.gettempdir(),
    APP_NAME, "app",
)
REAL_EXE = os.path.join(INSTALL_DIR, "SaldosScheduler.exe")
VERSION_FILE = os.path.join(INSTALL_DIR, ".version")


def _consola_para_instalar():
    """Solo cuando hace falta instalar/actualizar se abre una consola
    para mostrar el progreso de la descarga -si ya está todo al día, no
    se ve ninguna ventana de consola, igual que el programa real."""
    kernel32 = ctypes.windll.kernel32
    if kernel32.AllocConsole():
        sys.stdout = open("CONOUT$", "w")
        sys.stderr = open("CONOUT$", "w")


def _mensaje_error(texto):
    ctypes.windll.user32.MessageBoxW(0, texto, "Saldos Scheduler", 0x10)


def _reporte_descarga(bloques, tam_bloque, total):
    if total <= 0:
        return
    pct = min(100, bloques * tam_bloque * 100 // total)
    print(f"\rDescargando... {pct}%", end="", flush=True)


def _version_instalada():
    try:
        with open(VERSION_FILE, "r", encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return None


def _consultar_ultima_version():
    """Devuelve (tag, url_zip) de la última release publicada en GitHub,
    o None si no se pudo consultar (sin internet, GitHub caído, tardó
    más de CHECK_TIMEOUT, etc.) -en ese caso se sigue usando lo que ya
    esté instalado, sin bloquear al usuario."""
    try:
        req = urllib.request.Request(RELEASES_API, headers={"Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(req, timeout=CHECK_TIMEOUT) as resp:
            data = json.load(resp)
        tag = data.get("tag_name")
        assets = data.get("assets") or []
        zip_asset = next((a for a in assets if a.get("name", "").endswith(".zip")), None)
        if tag and zip_asset:
            return tag, zip_asset["browser_download_url"]
    except Exception:
        pass
    return None


def instalar(url, tag):
    _consola_para_instalar()
    print(f"Instalando Saldos Scheduler ({tag})...")
    os.makedirs(INSTALL_DIR, exist_ok=True)

    tmp_zip = os.path.join(tempfile.gettempdir(), "SaldosScheduler-app.zip")
    try:
        urllib.request.urlretrieve(url, tmp_zip, _reporte_descarga)
        print("\nDescomprimiendo...")
        with zipfile.ZipFile(tmp_zip, "r") as zf:
            zf.extractall(INSTALL_DIR)
        with open(VERSION_FILE, "w", encoding="utf-8") as f:
            f.write(tag)
    finally:
        try:
            os.remove(tmp_zip)
        except OSError:
            pass

    print("Instalación completa.")


def main():
    ya_instalado = os.path.isfile(REAL_EXE)
    ultima = _consultar_ultima_version()

    if not ya_instalado and ultima is None:
        _consola_para_instalar()
        print("\nERROR: no hay versión instalada y no se pudo consultar GitHub.")
        _mensaje_error(
            "No se pudo descargar Saldos Scheduler (sin conexión a internet "
            "o GitHub no respondió a tiempo).\n\n"
            "Conéctate a internet e inténtalo de nuevo."
        )
        sys.exit(1)

    if ultima is not None:
        tag, url = ultima
        if tag != _version_instalada():
            try:
                instalar(url, tag)
            except Exception as e:
                if ya_instalado:
                    # Ya había una versión funcionando: mejor abrir esa
                    # que dejar al usuario sin nada por un error de
                    # descarga (ej. se cortó la conexión a mitad).
                    print(f"\nNo se pudo actualizar ({e}); se abre la versión ya instalada.")
                else:
                    _consola_para_instalar()
                    print(f"\nERROR: no se pudo instalar la aplicación: {e}")
                    _mensaje_error(
                        "No se pudo descargar/instalar Saldos Scheduler.\n\n"
                        f"Detalle: {e}\n\n"
                        "Verifica tu conexión a internet e inténtalo de nuevo."
                    )
                    sys.exit(1)

    subprocess.Popen([REAL_EXE] + sys.argv[1:])


if __name__ == "__main__":
    main()
