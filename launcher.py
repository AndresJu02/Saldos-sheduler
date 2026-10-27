#!/usr/bin/env python3
"""
Lanzador de Saldos Scheduler.

Este es el único archivo que hace falta copiar a una PC nueva. La
primera vez que se ejecuta ahí, descarga el programa real (empaquetado
en modo carpeta -ver main.spec-) desde el Release "app-v1" de GitHub y
lo deja instalado en %LOCALAPPDATA%\\SaldosScheduler\\app; de ahí en
adelante, cada doble clic lo detecta ya instalado y lo abre directo, sin
volver a descargar ni descomprimir nada.

No se puede lograr esto empaquetando el programa real DENTRO de este
mismo lanzador (como si fuera un .exe de un solo archivo con todo
adentro): PyInstaller descomprime ese contenido entero en CADA arranque,
sin excepción -esa es la razón por la que el .exe original de un solo
archivo tardaba varios segundos en abrir-. Por eso el programa real se
descarga una sola vez y se deja instalado aparte, en vez de ir pegado a
este lanzador.

Para publicar una versión nueva del programa: subir un .zip actualizado
(SaldosScheduler.exe + _internal, sin archivos de configuración propios
de cada PC) como asset de un nuevo Release en GitHub, y actualizar
DOWNLOAD_URL más abajo. Los usuarios que ya lo tengan instalado no se
actualizan solos -hay que borrar la carpeta de instalación de cada PC
para forzar que se vuelva a descargar-.
"""
import os
import sys
import ctypes
import zipfile
import tempfile
import subprocess
import urllib.request

APP_NAME = "SaldosScheduler"
DOWNLOAD_URL = "https://github.com/AndresJu02/Saldos-sheduler/releases/download/app-v1/SaldosScheduler-app.zip"

INSTALL_DIR = os.path.join(
    os.environ.get("LOCALAPPDATA") or tempfile.gettempdir(),
    APP_NAME, "app",
)
REAL_EXE = os.path.join(INSTALL_DIR, "SaldosScheduler.exe")


def _consola_para_instalar():
    """Solo cuando hace falta instalar se abre una consola para mostrar
    el progreso de la descarga -en los arranques normales (ya instalado)
    no se ve ninguna ventana de consola, igual que el programa real."""
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


def instalar():
    _consola_para_instalar()
    print("Primera vez en esta PC: instalando Saldos Scheduler...")
    os.makedirs(INSTALL_DIR, exist_ok=True)

    tmp_zip = os.path.join(tempfile.gettempdir(), "SaldosScheduler-app.zip")
    try:
        urllib.request.urlretrieve(DOWNLOAD_URL, tmp_zip, _reporte_descarga)
        print("\nDescomprimiendo...")
        with zipfile.ZipFile(tmp_zip, "r") as zf:
            zf.extractall(INSTALL_DIR)
    finally:
        try:
            os.remove(tmp_zip)
        except OSError:
            pass

    print("Instalación completa.")


def main():
    if not os.path.isfile(REAL_EXE):
        try:
            instalar()
        except Exception as e:
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
