"""Pestaña "Notificaciones": configura y controla el monitor de avisos de
escritorio para Canales Abiertos (chats nuevos / mensajes nuevos). La
lógica del monitor vive en notificaciones_bitrix.py (raíz del proyecto),
no aquí — este archivo solo construye la pestaña."""
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout
from qfluentwidgets import LineEdit, PrimaryPushButton, PushButton, BodyLabel, TitleLabel, FluentIcon, HeaderCardWidget

from gui.process_control import ManagedProcess
from gui import dialogs as messagebox
from gui.dialogs import boton_ayuda
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
        import notificaciones_bitrix
        nf_cfg = notificaciones_bitrix.load_notificaciones_config()
    except Exception:
        nf_cfg = config.get("notificaciones_config", {})

    page = QWidget()
    layout = QVBoxLayout(page)
    layout.setContentsMargins(15, 15, 15, 15)
    layout.setSpacing(10)

    titulo = TitleLabel("🔔  Notificaciones de Canales Abiertos")
    layout.addWidget(titulo)

    nf_poll_edit = LineEdit()
    nf_poll_edit.setText(str(nf_cfg.get("poll_interval", 15)))

    def guardar_notificaciones():
        try:
            poll_interval = int(nf_poll_edit.text())
        except ValueError:
            messagebox.showerror("Error", "El intervalo de sondeo debe ser un número entero de segundos.")
            return

        config["notificaciones_config"] = {"poll_interval": poll_interval}
        save_config(config)
        messagebox.showinfo("Guardado", "Configuración de Notificaciones actualizada.")

    def probar_notificacion():
        try:
            from winotify import Notification
            Notification(
                app_id="Saldos Scheduler - Canales abiertos",
                title="Notificación de prueba",
                msg="Si ves esto en la bandeja de Windows, las notificaciones funcionan.",
                icon=str(base_dir / "icono_notificacion.png") if (base_dir / "icono_notificacion.png").exists() else "",
                duration="long",
            ).show()
        except Exception as e:
            messagebox.showerror("Error", f"No se pudo mostrar la notificación de prueba:\n{e}")

    btn_probar = PushButton(FluentIcon.RINGER, "Probar notificación")
    btn_probar.clicked.connect(probar_notificacion)
    btn_guardar = PrimaryPushButton(FluentIcon.SAVE, "Guardar configuración")
    btn_guardar.clicked.connect(guardar_notificaciones)

    fila_botones_guardar = QHBoxLayout()
    fila_botones_guardar.addStretch(1)
    fila_botones_guardar.addWidget(btn_probar)
    fila_botones_guardar.addWidget(btn_guardar)
    fila_botones_guardar_widget = QWidget()
    fila_botones_guardar_widget.setLayout(fila_botones_guardar)

    conexion_content = columna(
        texto_informativo("ℹ  El Webhook y el User ID de Bitrix24 se configuran una sola vez en la pestaña Configuración."),
        fila_campo(
            "Intervalo de sondeo (s):", nf_poll_edit,
            ayuda=boton_ayuda(
                "Revisa periódicamente los chats de \"Canales abiertos\" de Bitrix24 y muestra una "
                "notificación de Windows (como las de WhatsApp) cuando aparece un chat nuevo o llega "
                "un mensaje nuevo. No se notifican los mensajes que tú mismo envíes (según 'Mi User ID', "
                "pestaña Configuración).",
                titulo="Notificaciones de Canales abiertos",
            ),
        ),
        fila_botones_guardar_widget,
    )
    conexion_card = ctx.seccion_plegable(FluentIcon.LINK, "Conexión Bitrix24", conexion_content, "notificaciones.conexion")
    layout.addWidget(conexion_card)

    # ---------------- Control del monitor ----------------
    control_card = HeaderCardWidget("▶  Monitor")

    notif_status_label = BodyLabel()
    notif_status_label.setStyleSheet(f"color: {ACCENT}; font-weight: bold;")
    notif_status_var = LabelVar(notif_status_label)

    monitor = ctx.register_process(ManagedProcess(
        root=root,
        launch_cmd=lambda: ctx.get_launch_cmd(['--notificaciones']),
    )).bind(notif_status_var, on_change=lambda running: (
        btn_nf_iniciar.setEnabled(not running),
        btn_nf_detener.setEnabled(running),
    ))

    def nf_iniciar():
        if monitor.running:
            messagebox.showinfo("Notificaciones", "El monitor ya está en ejecución.")
            return
        if not config.get("bitrix_webhook_url", "").strip():
            messagebox.showerror("Error", "Configura primero la Webhook URL de Bitrix24 en la pestaña Configuración.")
            return

        monitor.start()
        messagebox.showinfo("Notificaciones", "Monitor iniciado en segundo plano.\nRevisa 'notificaciones_bitrix.log' para ver la actividad.")

    def _nf_autostart():
        # Este monitor se enciende solo al abrir la aplicación -ver
        # ctx.register_autostart() y gui/app.py-, sin diálogos: si falta
        # configuración simplemente no arranca, igual que si el usuario
        # nunca hubiera tocado el botón "Iniciar Monitor".
        if not monitor.running and config.get("bitrix_webhook_url", "").strip():
            monitor.start()

    ctx.register_autostart(_nf_autostart)

    def nf_detener():
        monitor.stop()
        messagebox.showinfo("Notificaciones", "Monitor detenido.")

    btn_nf_iniciar = PrimaryPushButton(FluentIcon.PLAY, "Iniciar Monitor")
    btn_nf_iniciar.clicked.connect(nf_iniciar)

    btn_nf_detener = PushButton(FluentIcon.PAUSE, "Detener Monitor")
    btn_nf_detener.setEnabled(False)
    btn_nf_detener.clicked.connect(nf_detener)

    btn_ver_log = crear_visor_log(base_dir / "notificaciones_bitrix.log", root)

    fila_botones = QHBoxLayout()
    fila_botones.addWidget(btn_nf_iniciar)
    fila_botones.addWidget(btn_nf_detener)
    fila_botones.addSpacing(10)
    fila_botones.addWidget(notif_status_label)
    fila_botones.addStretch(1)
    fila_botones.addWidget(btn_ver_log)
    fila_botones_widget = QWidget()
    fila_botones_widget.setLayout(fila_botones)

    monitor_content = columna(fila_botones_widget, btn_ver_log.panel, margins=(24, 16, 24, 20))
    control_card.viewLayout.addWidget(monitor_content)
    layout.addWidget(control_card)

    layout.addStretch(1)
    return page
