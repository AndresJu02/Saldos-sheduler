"""Pestaña "Renovación DIDWW": configura y controla el monitor que
renueva automáticamente líneas DIDWW cuando alguien las pide "Renovar"
en el chat de Bitrix24. La lógica del monitor vive en
didww_renovacion.py (raíz del proyecto), no aquí — este archivo solo
construye la pestaña."""
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout
from qfluentwidgets import LineEdit, PrimaryPushButton, PushButton, BodyLabel, FluentIcon, HeaderCardWidget

from gui.process_control import ManagedProcess
from gui import dialogs as messagebox
from gui.dialogs import boton_ayuda, checkbox_modo_prueba
from gui.qt_compat import LabelVar
from gui.log_viewer import crear_visor_log
from gui.widgets import fila_campo, texto_informativo, columna


def build(ctx):
    root = ctx.root
    ACCENT = ctx.ACCENT
    config = ctx.config
    save_config = ctx.save_config
    base_dir = ctx.base_dir

    try:
        import didww_renovacion
        dw_cfg = didww_renovacion.load_didww_renovacion_config()
    except Exception:
        dw_cfg = config.get("didww_renovacion_config", {})

    page = QWidget()
    layout = QVBoxLayout(page)
    layout.setContentsMargins(15, 15, 15, 15)
    layout.setSpacing(10)

    dw_chat_edit = LineEdit()
    dw_chat_edit.setPlaceholderText("ej. chat142")
    dw_chat_edit.setText(dw_cfg.get("chat_id", ""))

    dw_poll_edit = LineEdit()
    dw_poll_edit.setText(str(dw_cfg.get("poll_interval", 30)))

    dw_dry_run_chk = checkbox_modo_prueba(bool(dw_cfg.get("dry_run", True)))

    def guardar_didww():
        try:
            poll_interval = int(dw_poll_edit.text())
        except ValueError:
            messagebox.showerror("Error", "El intervalo de sondeo debe ser un número entero de segundos.")
            return
        if not dw_chat_edit.text().strip():
            messagebox.showerror("Error", "Indica el ID del chat de renovaciones de Bitrix24.")
            return

        config["didww_renovacion_config"] = {
            "chat_id": dw_chat_edit.text().strip(),
            "poll_interval": poll_interval,
            "dry_run": bool(dw_dry_run_chk.isChecked()),
        }
        save_config(config)
        messagebox.showinfo("Guardado", "Configuración de Renovación DIDWW actualizada.")

    btn_guardar = PrimaryPushButton(FluentIcon.SAVE, "Guardar configuración")
    btn_guardar.clicked.connect(guardar_didww)
    fila_guardar = QHBoxLayout()
    fila_guardar.addStretch(1)
    fila_guardar.addWidget(btn_guardar)
    fila_guardar_widget = QWidget()
    fila_guardar_widget.setLayout(fila_guardar)

    conexion_content = columna(
        texto_informativo("ℹ  El Webhook de Bitrix24 ahora se configura una sola vez en la pestaña Configuración."),
        fila_campo(
            "Chat de renovaciones (ID):", dw_chat_edit,
            ayuda=boton_ayuda(
                "La API Key de DIDWW no se configura aquí: se reutiliza la misma que ya está guardada "
                "en la pestaña Proveedores → DIDWW → Configurar.",
                titulo="API Key de DIDWW",
            ),
        ),
        fila_campo("Intervalo de sondeo (s):", dw_poll_edit),
        fila_campo(
            "", dw_dry_run_chk,
            ayuda=boton_ayuda(
                "Detecta y registra qué haría en el log, sin modificar nada en DIDWW y SIN escribir nada en el "
                "chat de Bitrix24.",
                titulo="Modo de prueba (dry-run)",
            ),
        ),
        fila_guardar_widget,
    )
    conexion_card = ctx.seccion_plegable(FluentIcon.LINK, "Conexión Bitrix24", conexion_content, "didww.conexion")
    layout.addWidget(conexion_card)

    # ---------------- Control del monitor ----------------
    control_card = HeaderCardWidget("▶  Monitor")

    didww_renov_status_label = BodyLabel()
    didww_renov_status_label.setStyleSheet(f"color: {ACCENT}; font-weight: bold;")
    didww_renov_status_var = LabelVar(didww_renov_status_label)

    monitor = ctx.register_process(ManagedProcess(
        root=root,
        launch_cmd=lambda: ctx.get_launch_cmd(['--didww-renovacion']),
    )).bind(didww_renov_status_var, on_change=lambda running: (
        btn_dw_iniciar.setEnabled(not running),
        btn_dw_detener.setEnabled(running),
    ))

    def dw_iniciar():
        if monitor.running:
            messagebox.showinfo("Renovación DIDWW", "El monitor ya está en ejecución.")
            return
        if not config.get("bitrix_webhook_url", "").strip():
            messagebox.showerror("Error", "Configura primero la Webhook URL de Bitrix24 en la pestaña Configuración.")
            return
        if not config.get("didww_renovacion_config", {}).get("chat_id", "").strip():
            messagebox.showerror("Error", "Configura primero el Chat de renovaciones y guarda los cambios.")
            return
        if not config.get("providers_config", {}).get("DIDWW", {}).get("api_key", "").strip():
            messagebox.showerror(
                "Falta la API Key de DIDWW",
                "Configura la API Key en la pestaña Proveedores → DIDWW → Configurar antes de iniciar este monitor."
            )
            return

        monitor.start()
        messagebox.showinfo("Renovación DIDWW", "Monitor iniciado en segundo plano.\nRevisa 'didww_renovacion.log' para ver la actividad.")

    def dw_detener():
        monitor.stop()
        messagebox.showinfo("Renovación DIDWW", "Monitor detenido.")

    btn_dw_iniciar = PrimaryPushButton(FluentIcon.PLAY, "Iniciar Monitor")
    btn_dw_iniciar.clicked.connect(dw_iniciar)

    btn_dw_detener = PushButton(FluentIcon.PAUSE, "Detener Monitor")
    btn_dw_detener.setEnabled(False)
    btn_dw_detener.clicked.connect(dw_detener)

    btn_ver_log = crear_visor_log(base_dir / "didww_renovacion.log", root)

    fila_botones = QHBoxLayout()
    fila_botones.addWidget(btn_dw_iniciar)
    fila_botones.addWidget(btn_dw_detener)
    fila_botones.addSpacing(10)
    fila_botones.addWidget(didww_renov_status_label)
    fila_botones.addStretch(1)
    fila_botones.addWidget(btn_ver_log)
    fila_botones_widget = QWidget()
    fila_botones_widget.setLayout(fila_botones)

    monitor_content = columna(fila_botones_widget, btn_ver_log.panel, margins=(24, 16, 24, 20))
    control_card.viewLayout.addWidget(monitor_content)
    layout.addWidget(control_card)

    layout.addStretch(1)
    return page
