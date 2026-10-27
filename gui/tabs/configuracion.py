"""Pestaña "Configuración": credenciales de Google, URL de la hoja de
cálculo y actualización de Chrome/ChromeDriver.

Siempre se agrega de última en gui/app.py, para que quede como la
pestaña más a la derecha del notebook."""
import threading
import webbrowser
import tkinter as tk
from tkinter import ttk, filedialog

from core.chrome_tools import get_installed_versions, update_chrome_stack
from gui import dialogs as messagebox
from gui.dialogs import boton_ayuda


def build(notebook, ctx):
    root = ctx.root
    BG = ctx.BG
    config = ctx.config
    save_config = ctx.save_config

    tab_config = ttk.Frame(notebook)
    notebook.add(tab_config, text="   Configuración   ")

    # ---------------- Webhook de Bitrix24 (compartido) ----------------
    # Cargas VOIP, Tickets, Renovación DIDWW y Notificaciones usan este
    # mismo Webhook -antes cada pestaña tenía su propio campo, pero las 4
    # terminaban apuntando a la misma cuenta de Bitrix24-. Se configura
    # una sola vez aquí.
    bitrix_wrapper, bitrix_frame = ctx.seccion_plegable(tab_config, "🔌  Webhook de Bitrix24", "configuracion.bitrix")
    bitrix_wrapper.pack(fill="x", padx=10, pady=(15, 10))

    bitrix_webhook_var = tk.StringVar(value=config.get("bitrix_webhook_url", ""))

    def guardar_bitrix_webhook():
        config["bitrix_webhook_url"] = bitrix_webhook_var.get().strip()
        save_config(config)
        messagebox.showinfo("Guardado", "Webhook de Bitrix24 actualizado.")

    def ayuda_webhook_bitrix():
        win = tk.Toplevel(root)
        win.title("Cómo obtener el Webhook URL de Bitrix24")
        win.geometry("600x480")
        win.configure(bg=BG)
        texto = (
            "1. Entra a tu portal de Bitrix24 (ej: tuempresa.bitrix24.es).\n"
            "2. En el menú lateral, ve a 'Más' → 'Mercado' (o busca 'Webhooks' con la "
            "lupa de arriba).\n"
            "3. Entra a 'Desarrollador' → 'Otro' → 'Webhooks'.\n"
            "4. Pestaña 'Entrante' → botón 'Agregar webhook entrante'.\n\n"
            "PERMISOS:\n"
            "5. En 'Permisos de acceso (scope)', marca al menos "
            "'Mensajería instantánea y chats (im)' -es lo único que usan Cargas VOIP, "
            "Tickets, Renovación DIDWW y Notificaciones-.\n"
            "6. Guarda el webhook.\n\n"
            "COPIAR LA URL:\n"
            "7. Bitrix24 te muestra una URL con esta forma:\n"
            "   https://tuempresa.bitrix24.es/rest/13/xxxxxxxxxxxxxxxx/\n"
            "8. Copia esa URL completa y pégala aquí arriba, en 'Webhook URL'.\n\n"
            "Nota: el número después de /rest/ es el ID del usuario de Bitrix24 que queda "
            "'dueño' del webhook -las acciones automáticas (like a tickets, respuestas en "
            "el chat, etc.) quedan registradas como hechas por ese usuario-."
        )
        lbl = ttk.Label(win, text=texto, justify="left", wraplength=560)
        lbl.pack(padx=15, pady=15)
        ttk.Button(win, text="Cerrar", command=win.destroy).pack(pady=10)

    row_bitrix = ttk.Frame(bitrix_frame)
    row_bitrix.pack(fill="x")
    ttk.Label(row_bitrix, text="Webhook URL:", font=("Segoe UI", 10, "bold")).pack(side="left", padx=(0, 8))
    ttk.Entry(row_bitrix, textvariable=bitrix_webhook_var).pack(side="left", expand=True, fill="x", padx=(0, 10))
    ttk.Button(row_bitrix, text="Guardar", command=guardar_bitrix_webhook, style="Accent.TButton").pack(side="left", padx=3)
    ttk.Button(row_bitrix, text="❓  ¿De dónde lo saco?", command=ayuda_webhook_bitrix).pack(side="left", padx=3)
    ttk.Label(
        bitrix_frame,
        text="Lo usan: Cargas VOIP, Tickets, Renovación DIDWW y 🔔 Notificaciones.",
        foreground="#9399b2", font=("Segoe UI", 9)
    ).pack(anchor="w", pady=(8, 0))

    cred_wrapper, cred_frame = ctx.seccion_plegable(tab_config, "🔑  Archivo de credenciales", "configuracion.credenciales")
    cred_wrapper.pack(fill="x", padx=10, pady=(0, 10))

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

    # ---------------- API Key DIDWW ----------------
    # La MISMA clave que usan Proveedores -> DIDWW -> Configurar, Renovación
    # DIDWW y Tienda DID (todas leen/escriben providers_config.DIDWW.api_key
    # en scheduler_config.json). Cambiarla aquí la refleja allá, y viceversa:
    # como config es el mismo dict compartido en memoria, un cambio hecho
    # desde el diálogo de Proveedores ya queda guardado ahí; lo único que
    # hace falta es refrescar esta entrada cuando se vuelve a esta pestaña
    # (por si se cambió en Proveedores mientras tanto), con el bind de
    # <<NotebookTabChanged>> más abajo.
    didww_wrapper, didww_frame = ctx.seccion_plegable(tab_config, "🔐  API Key de DIDWW", "configuracion.didww_key")
    didww_wrapper.pack(fill="x", padx=10, pady=(0, 10))

    didww_key_var = tk.StringVar(value=config.get("providers_config", {}).get("DIDWW", {}).get("api_key", ""))

    def guardar_didww_key():
        valor = didww_key_var.get().strip()
        config.setdefault("providers_config", {}).setdefault("DIDWW", {})["api_key"] = valor
        save_config(config)
        messagebox.showinfo("Guardado", "API Key de DIDWW actualizada.")

    def refrescar_didww_key():
        didww_key_var.set(config.get("providers_config", {}).get("DIDWW", {}).get("api_key", ""))

    row_didww = ttk.Frame(didww_frame)
    row_didww.pack(fill="x")
    boton_ayuda(
        row_didww,
        "Se comparte con Proveedores → DIDWW → Configurar, Renovación DIDWW y Tienda DID: cambiarla "
        "aquí la actualiza en todos lados, y cambiarla desde Proveedores también se ve reflejado aquí.",
        titulo="API Key de DIDWW",
    ).pack(side="left", padx=(0, 6))
    ttk.Label(row_didww, text="API Key:", font=("Segoe UI", 10, "bold")).pack(side="left", padx=(0, 8))
    ttk.Entry(row_didww, textvariable=didww_key_var).pack(side="left", expand=True, fill="x", padx=(0, 10))
    ttk.Button(row_didww, text="Guardar", command=guardar_didww_key, style="Accent.TButton").pack(side="left", padx=3)

    notebook.bind(
        "<<NotebookTabChanged>>",
        lambda e: refrescar_didww_key() if notebook.select() == str(tab_config) else None,
        add="+"
    )

    url_wrapper, url_frame = ctx.seccion_plegable(tab_config, "🌐  URL de Google Sheets", "configuracion.google_sheets")
    url_wrapper.pack(fill="x", padx=10, pady=(0, 10))

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
    driver_wrapper, driver_frame = ctx.seccion_plegable(tab_config, "🧭  Chrome / ChromeDriver", "configuracion.chrome_driver")
    driver_wrapper.pack(fill="x", padx=10, pady=(0, 10))

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
