"""Pestaña "Configuración": credenciales de Google, URL de la hoja de
cálculo y actualización de Chrome/ChromeDriver.

Siempre se agrega de última en gui/app.py, para que quede como la
pestaña más a la derecha del selector."""
import threading

from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QFileDialog
from qfluentwidgets import LineEdit, PrimaryPushButton, PushButton, BodyLabel, FluentIcon

from core.chrome_tools import get_installed_versions, update_chrome_stack
from gui import dialogs as messagebox
from gui.dialogs import boton_ayuda, mostrar_ayuda_larga
from gui.widgets import fila_campo, texto_informativo, columna, pagina_con_scroll


def build(ctx):
    root = ctx.root
    BG = ctx.BG
    config = ctx.config
    save_config = ctx.save_config

    page, layout = pagina_con_scroll(BG)

    # ---------------- Conexión Bitrix24 (compartida) ----------------
    # Cargas VOIP, Tickets, Renovación DIDWW y Notificaciones usan este
    # mismo Webhook y User ID -antes cada pestaña tenía su propio campo,
    # pero todas terminaban apuntando a la misma cuenta de Bitrix24-. Se
    # configura una sola vez aquí.
    bitrix_webhook_edit = LineEdit()
    bitrix_webhook_edit.setText(config.get("bitrix_webhook_url", ""))
    bitrix_user_id_edit = LineEdit()
    bitrix_user_id_edit.setText(str(config.get("mi_user_id", "13")))

    def guardar_bitrix_webhook():
        config["bitrix_webhook_url"] = bitrix_webhook_edit.text().strip()
        config["mi_user_id"] = bitrix_user_id_edit.text().strip() or "13"
        save_config(config)
        messagebox.showinfo("Guardado", "Conexión de Bitrix24 actualizada.")

    def ayuda_webhook_bitrix():
        mostrar_ayuda_larga(
            "Cómo obtener el Webhook URL de Bitrix24",
            "1. Entra a tu portal de Bitrix24 (ej: tuempresa.bitrix24.es).\n"
            "2. En el menú lateral, ve a 'Más' → 'Mercado' (o busca 'Webhooks' con la lupa de arriba).\n"
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

    btn_ayuda_webhook = PushButton("¿De dónde lo saco?")
    btn_ayuda_webhook.clicked.connect(ayuda_webhook_bitrix)
    btn_guardar_bitrix = PrimaryPushButton(FluentIcon.SAVE, "Guardar")
    btn_guardar_bitrix.clicked.connect(guardar_bitrix_webhook)

    bitrix_content = columna(
        fila_campo("Webhook URL:", bitrix_webhook_edit, extra=btn_ayuda_webhook),
        fila_campo(
            "Mi User ID:", bitrix_user_id_edit, extra=btn_guardar_bitrix,
            ayuda=boton_ayuda(
                "Lo usan Tickets (para no darle like a sus propias asignaciones) y 🔔 Notificaciones "
                "(para no notificarte tus propios mensajes). Es el ID numérico del usuario de Bitrix24 "
                "dueño del Webhook -el mismo número que aparece después de /rest/ en la URL de arriba-.",
                titulo="Mi User ID",
            ),
        ),
        texto_informativo("Lo usan: Cargas VOIP, Tickets, Renovación DIDWW y 🔔 Notificaciones."),
    )
    bitrix_card = ctx.seccion_plegable(FluentIcon.LINK, "Conexión Bitrix24", bitrix_content, "configuracion.bitrix")
    layout.addWidget(bitrix_card)

    # ---------------- Archivo de credenciales ----------------
    cred_path_edit = LineEdit()
    cred_path_edit.setText(config.get("credentials_path", ""))

    def seleccionar_credenciales():
        path, _ = QFileDialog.getOpenFileName(root, "Seleccionar credenciales.json", "", "JSON (*.json);;Todos (*.*)")
        if path:
            cred_path_edit.setText(path)
            config["credentials_path"] = path
            save_config(config)

    def ayuda_credenciales():
        mostrar_ayuda_larga(
            "Cómo obtener credenciales.json",
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
            "Luego, en esta aplicación, selecciona ese archivo con el botón 'Examinar'.",
            enlace="https://console.cloud.google.com/",
            texto_enlace="Abrir Google Cloud Console",
        )

    btn_examinar_cred = PrimaryPushButton(FluentIcon.FOLDER, "Examinar")
    btn_examinar_cred.clicked.connect(seleccionar_credenciales)
    btn_ayuda_cred = PushButton("Ayuda")
    btn_ayuda_cred.clicked.connect(ayuda_credenciales)

    fila_cred_botones = QHBoxLayout()
    fila_cred_botones.addWidget(btn_examinar_cred)
    fila_cred_botones.addWidget(btn_ayuda_cred)
    fila_cred_botones_widget = QWidget()
    fila_cred_botones_widget.setLayout(fila_cred_botones)

    cred_content = columna(
        fila_campo("Ruta:", cred_path_edit, extra=fila_cred_botones_widget),
    )
    cred_card = ctx.seccion_plegable(FluentIcon.CERTIFICATE, "Archivo de credenciales", cred_content, "configuracion.credenciales")
    layout.addWidget(cred_card)

    # ---------------- API Key DIDWW ----------------
    # La MISMA clave que usan Proveedores -> DIDWW -> Configurar, Renovación
    # DIDWW y Tienda DID (todas leen/escriben providers_config.DIDWW.api_key
    # en scheduler_config.json). Cambiarla aquí la refleja allá, y viceversa:
    # como config es el mismo dict compartido en memoria, un cambio hecho
    # desde el diálogo de Proveedores ya queda guardado ahí; lo único que
    # hace falta es refrescar esta entrada cuando se vuelve a esta pestaña
    # (por si se cambió en Proveedores mientras tanto) -ver page.on_activated
    # al final de esta función-.
    didww_key_edit = LineEdit()
    didww_key_edit.setText(config.get("providers_config", {}).get("DIDWW", {}).get("api_key", ""))

    def guardar_didww_key():
        valor = didww_key_edit.text().strip()
        config.setdefault("providers_config", {}).setdefault("DIDWW", {})["api_key"] = valor
        save_config(config)
        messagebox.showinfo("Guardado", "API Key de DIDWW actualizada.")

    def refrescar_didww_key():
        didww_key_edit.setText(config.get("providers_config", {}).get("DIDWW", {}).get("api_key", ""))

    btn_guardar_didww = PrimaryPushButton(FluentIcon.SAVE, "Guardar")
    btn_guardar_didww.clicked.connect(guardar_didww_key)

    didww_content = columna(
        fila_campo(
            "API Key:", didww_key_edit, extra=btn_guardar_didww,
            ayuda=boton_ayuda(
                "Se comparte con Proveedores → DIDWW → Configurar, Renovación DIDWW y Tienda DID: cambiarla "
                "aquí la actualiza en todos lados, y cambiarla desde Proveedores también se ve reflejado aquí.",
                titulo="API Key de DIDWW",
            ),
        ),
    )
    didww_card = ctx.seccion_plegable(FluentIcon.VPN, "API Key de DIDWW", didww_content, "configuracion.didww_key")
    layout.addWidget(didww_card)

    # ---------------- URL de Google Sheets ----------------
    sheet_url_edit = LineEdit()
    sheet_url_edit.setText(config.get("google_sheet_url", ""))

    def guardar_url():
        config["google_sheet_url"] = sheet_url_edit.text().strip()
        save_config(config)
        messagebox.showinfo("Guardado", "URL de Google Sheets actualizada.")

    btn_guardar_url = PrimaryPushButton(FluentIcon.SAVE, "Guardar URL")
    btn_guardar_url.clicked.connect(guardar_url)

    url_content = columna(
        fila_campo("URL:", sheet_url_edit, extra=btn_guardar_url),
    )
    url_card = ctx.seccion_plegable(FluentIcon.GLOBE, "URL de Google Sheets", url_content, "configuracion.google_sheets")
    layout.addWidget(url_card)

    # ---------------- ChromeDriver / Chrome ----------------
    def _version_label_text():
        v_chrome, v_driver = get_installed_versions()
        v_chrome = v_chrome or "no instalado"
        v_driver = v_driver or "no instalado"
        return f"Chrome: {v_chrome}      ChromeDriver: {v_driver}"

    driver_version_label = BodyLabel(_version_label_text())

    def actualizar_chromedriver():
        respuesta = messagebox.askyesno(
            "Actualizar ChromeDriver",
            "Esto descargará la última versión estable de Chrome portable y "
            "ChromeDriver, reemplazando la instalación actual.\n\n¿Continuar?"
        )
        if not respuesta:
            return

        btn_actualizar_driver.setEnabled(False)
        btn_actualizar_driver.setText("Actualizando...")
        driver_version_label.setText("Consultando última versión disponible...")

        def tarea():
            try:
                version, _, _ = update_chrome_stack()
                root.after(0, lambda: (
                    driver_version_label.setText(_version_label_text()),
                    messagebox.showinfo("Actualizado", f"Chrome y ChromeDriver actualizados a la versión {version}.")
                ))
            except Exception as e:
                root.after(0, lambda: (
                    driver_version_label.setText(_version_label_text()),
                    messagebox.showerror("Error", f"No se pudo actualizar ChromeDriver:\n{e}")
                ))
            finally:
                root.after(0, lambda: (btn_actualizar_driver.setEnabled(True), btn_actualizar_driver.setText("Actualizar a la última versión")))

        threading.Thread(target=tarea, daemon=True).start()

    btn_actualizar_driver = PrimaryPushButton(FluentIcon.SYNC, "Actualizar a la última versión")
    btn_actualizar_driver.clicked.connect(actualizar_chromedriver)

    driver_content = columna(
        fila_campo("", driver_version_label, extra=btn_actualizar_driver),
    )
    driver_card = ctx.seccion_plegable(FluentIcon.DEVELOPER_TOOLS, "Chrome / ChromeDriver", driver_content, "configuracion.chrome_driver")
    layout.addWidget(driver_card)

    layout.addStretch(1)
    page.on_activated = refrescar_didww_key  # ver gui/app.py: _add_tab llama esto al mostrar la pestaña
    return page
