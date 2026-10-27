#!/usr/bin/env python3
"""
Herramienta de desarrollo para publicar una actualización del programa.

NO es parte de Saldos Scheduler ni se distribuye al equipo -es solo para
quien mantiene el código (esta PC, con Python y el proyecto completo)-.
Automatiza lo que antes había que hacer a mano cada vez que se quería
que el lanzador (launcher.py) ofreciera una versión nueva:

  1. Reconstruye el programa (python -m PyInstaller main.spec).
  2. Arma el .zip con SaldosScheduler.exe + _internal (sin config ni
     credenciales de esta PC).
  3. Calcula el siguiente tag de versión (app-v2, app-v3, ...) mirando
     los Releases que ya existen en GitHub.
  4. Crea el Release nuevo y sube el .zip como adjunto.

El token de GitHub se obtiene del mismo lugar de donde Git ya lo saca
para hacer `git push` (Administrador de credenciales de Windows) -no
hace falta pegar ningún token a mano-.

Uso: hacer doble clic en "Publicar actualización.bat", o ejecutar
`python publicar_actualizacion.py` desde la carpeta del proyecto.
"""
import os
import re
import sys
import json
import zipfile
import threading
import subprocess
import tkinter as tk
from tkinter import ttk

import requests

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
REPO = "AndresJu02/Saldos-sheduler"
ZIP_NAME = "SaldosScheduler-app.zip"


def log_a(widget, texto):
    widget.configure(state="normal")
    widget.insert("end", texto + "\n")
    widget.see("end")
    widget.configure(state="disabled")
    widget.update_idletasks()


def obtener_token():
    """Mismo token que ya usa `git push` en este equipo, vía el
    Administrador de credenciales de Windows -no se guarda ni se pide
    nada nuevo-."""
    entrada = "protocol=https\nhost=github.com\n\n"
    resultado = subprocess.run(
        ["git", "credential", "fill"],
        input=entrada, capture_output=True, text=True, cwd=BASE_DIR,
    )
    for linea in resultado.stdout.splitlines():
        if linea.startswith("password="):
            return linea.split("=", 1)[1].strip()
    raise RuntimeError(
        "No se encontró un token de GitHub guardado. Haz un `git push` manual "
        "una vez desde esta PC para que Windows lo recuerde, y vuelve a intentar."
    )


def siguiente_tag():
    resp = requests.get(f"https://api.github.com/repos/{REPO}/releases", timeout=15)
    resp.raise_for_status()
    numeros = [0]
    for r in resp.json():
        m = re.fullmatch(r"app-v(\d+)", r.get("tag_name", ""))
        if m:
            numeros.append(int(m.group(1)))
    return f"app-v{max(numeros) + 1}"


def commit_actual():
    """Hash corto del commit actual, o None si no se pudo leer. Si hay
    cambios sin confirmar (git status sucio), se le agrega "-dirty": el
    build de PyInstaller usa los archivos tal como están en el disco, no
    el último commit, así que unos cambios sin guardar SÍ hacen que el
    paquete sea distinto aunque el commit sea el mismo -no deben tratarse
    como "ya publicado esto"."""
    resultado = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=BASE_DIR, capture_output=True, text=True,
    )
    if resultado.returncode != 0:
        return None
    commit = resultado.stdout.strip()

    # --untracked-files=no: un archivo suelto sin agregar a git (ej. una
    # captura de pantalla cualquiera en el proyecto) no afecta lo que
    # arma PyInstaller, así que no debe contar como "cambio pendiente".
    estado = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=no"],
        cwd=BASE_DIR, capture_output=True, text=True,
    )
    if estado.returncode == 0 and estado.stdout.strip():
        commit += "-dirty"
    return commit


def commit_de_ultima_release():
    """Lee el commit guardado en el cuerpo de la última publicación (ver
    el "Commit: <hash>" que arma publicar() más abajo), para poder avisar
    si no cambió nada de código desde esa vez. Si la última release es
    vieja y no tiene ese dato (ej. "app-v1", publicada a mano antes de
    que existiera esto), devuelve None -no hay con qué comparar-."""
    resp = requests.get(f"https://api.github.com/repos/{REPO}/releases/latest", timeout=15)
    if resp.status_code != 200:
        return None
    body = resp.json().get("body") or ""
    m = re.search(r"Commit:\s*([0-9a-f]+)", body)
    return m.group(1) if m else None


def reconstruir(widget):
    log_a(widget, "Reconstruyendo el programa (puede tardar un minuto)...")
    resultado = subprocess.run(
        [sys.executable, "-m", "PyInstaller", "main.spec", "--noconfirm"],
        cwd=BASE_DIR, capture_output=True, text=True,
    )
    if resultado.returncode != 0:
        log_a(widget, resultado.stdout[-2000:])
        log_a(widget, resultado.stderr[-2000:])
        raise RuntimeError("Falló la reconstrucción del programa (ver detalle arriba).")
    log_a(widget, "Reconstrucción lista.")


def armar_zip(widget):
    log_a(widget, "Armando el paquete (.zip)...")
    dist_dir = os.path.join(BASE_DIR, "dist")
    exe_path = os.path.join(dist_dir, "SaldosScheduler.exe")
    internal_dir = os.path.join(dist_dir, "_internal")
    if not os.path.isfile(exe_path) or not os.path.isdir(internal_dir):
        raise RuntimeError(
            "No se encontró dist/SaldosScheduler.exe + dist/_internal tras reconstruir. "
            "¿Sigue main.spec en modo carpeta (onedir)?"
        )

    zip_path = os.path.join(dist_dir, ZIP_NAME)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        zf.write(exe_path, "SaldosScheduler.exe")
        for root, _dirs, files in os.walk(internal_dir):
            for nombre in files:
                full = os.path.join(root, nombre)
                arcname = os.path.relpath(full, dist_dir)
                zf.write(full, arcname)
    log_a(widget, f"Paquete listo: {zip_path} ({os.path.getsize(zip_path) / 1_000_000:.1f} MB)")
    return zip_path


def publicar(widget, zip_path, token, commit):
    tag = siguiente_tag()
    log_a(widget, f"Creando Release '{tag}' en GitHub...")
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}

    cuerpo = "Publicado automáticamente con publicar_actualizacion.py. No incluye configuración ni credenciales."
    if commit:
        cuerpo += f"\n\nCommit: {commit}"

    resp = requests.post(
        f"https://api.github.com/repos/{REPO}/releases",
        headers=headers,
        json={
            "tag_name": tag,
            "name": f"SaldosScheduler app {tag}",
            "body": cuerpo,
            "draft": False,
            "prerelease": False,
        },
        timeout=30,
    )
    resp.raise_for_status()
    release = resp.json()

    log_a(widget, "Subiendo el paquete (puede tardar según tu conexión)...")
    upload_url = release["upload_url"].split("{")[0]
    with open(zip_path, "rb") as f:
        datos = f.read()
    resp = requests.post(
        upload_url,
        headers={**headers, "Content-Type": "application/zip"},
        params={"name": ZIP_NAME},
        data=datos,
        timeout=600,
    )
    resp.raise_for_status()

    log_a(widget, "")
    log_a(widget, f"¡Listo! Publicado como {tag}.")
    log_a(widget, release["html_url"])
    log_a(widget, "")
    log_a(widget, "El lanzador de cada PC va a detectar esta versión solo, la próxima vez que se abra.")


def ejecutar(widget, boton, commit):
    try:
        reconstruir(widget)
        zip_path = armar_zip(widget)
        token = obtener_token()
        publicar(widget, zip_path, token, commit)
    except Exception as e:
        log_a(widget, "")
        log_a(widget, f"ERROR: {e}")
    finally:
        boton.configure(state="normal", text="📦  Publicar actualización")


def main():
    root = tk.Tk()
    root.title("Publicar actualización - Saldos Scheduler")
    root.geometry("700x420")
    root.configure(bg="#1e1e2e")

    try:
        sys.path.insert(0, BASE_DIR)
        from gui.theme import apply_dark_theme, apply_dark_titlebar
        apply_dark_titlebar(root)
        apply_dark_theme(root)
    except Exception:
        pass

    ttk.Label(
        root,
        text="Reconstruye el programa, arma el paquete y lo publica como Release nuevo en GitHub.\n"
             "El lanzador de cada PC lo va a detectar solo en su próximo arranque.",
        wraplength=660, justify="left", font=("Segoe UI", 10),
    ).pack(padx=15, pady=(15, 10), anchor="w")

    log_widget = tk.Text(root, height=15, state="disabled", bg="#181825", fg="#cdd6f4",
                          font=("Consolas", 9), wrap="word")
    log_widget.pack(fill="both", expand=True, padx=15, pady=(0, 10))

    def preguntar(titulo, texto):
        try:
            from gui import dialogs as messagebox
            return messagebox.askyesno(titulo, texto)
        except Exception:
            import tkinter.messagebox as mb
            return mb.askyesno(titulo, texto)

    def al_hacer_clic():
        commit = commit_actual()

        try:
            commit_publicado = commit_de_ultima_release()
        except Exception:
            commit_publicado = None  # sin internet, GitHub caído, etc.: no se puede comparar, se sigue igual

        if commit and commit_publicado and commit == commit_publicado:
            log_a(log_widget, f"Nota: el código actual (commit {commit}) es el mismo que ya está "
                               f"publicado -no hubo cambios desde la última vez-.")
            if not preguntar(
                "Sin cambios desde la última publicación",
                f"El código de esta PC (commit {commit}) es el mismo que ya publicaste la última vez. "
                "Publicar de nuevo crearía una versión idéntica a la que ya tiene el lanzador.\n\n"
                "¿Publicar de todas formas?",
            ):
                return
        elif not preguntar(
            "Confirmar publicación",
            "Esto va a crear un Release público nuevo en GitHub con la versión actual "
            "del programa. ¿Continuar?",
        ):
            return

        boton.configure(state="disabled", text="Publicando...")
        threading.Thread(target=ejecutar, args=(log_widget, boton, commit), daemon=True).start()

    boton = ttk.Button(root, text="📦  Publicar actualización", command=al_hacer_clic, style="Accent.TButton")
    boton.pack(pady=(0, 15))

    root.mainloop()


if __name__ == "__main__":
    main()
