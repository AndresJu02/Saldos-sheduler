"""Pestaña "Configuración": credenciales de Google, URL de la hoja de
cálculo y actualización de Chrome/ChromeDriver.

Siempre se agrega de última en gui/app.py, para que quede como la
pestaña más a la derecha del notebook."""
import threading
import webbrowser
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

from core.chrome_tools import get_installed_versions, update_chrome_stack


def build(notebook, ctx):
    root = ctx.root
    BG = ctx.BG
    config = ctx.config
    save_config = ctx.save_config

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
