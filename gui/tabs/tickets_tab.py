"""Pestaña "Tickets": configura y controla el monitor de auto-like de
tickets asignados. La lógica del monitor vive en auto_like_tickets.py
(raíz del proyecto), no aquí — este archivo solo construye la pestaña."""
from pathlib import Path

from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout
from qfluentwidgets import LineEdit, PrimaryPushButton, PushButton, BodyLabel, FluentIcon, HeaderCardWidget

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
        import auto_like_tickets
        al_cfg = auto_like_tickets.load_auto_like_config()
    except Exception:
        al_cfg = config.get("auto_like_tickets_config", {})

    page = QWidget()
    layout = QVBoxLayout(page)
    layout.setContentsMargins(15, 15, 15, 15)
    layout.setSpacing(10)

    # ---------------- Conexión Bitrix24 ----------------
    al_chat_edit = LineEdit()
    al_chat_edit.setText(al_cfg.get("tickets_chat_id", "chat97"))

    al_poll_edit = LineEdit()
    al_poll_edit.setText(str(al_cfg.get("poll_interval", 20)))

    def guardar_tickets():
        try:
            poll_interval = int(al_poll_edit.text())
        except ValueError:
            messagebox.showerror("Error", "El intervalo de sondeo debe ser un número entero de segundos.")
            return

        config["auto_like_tickets_config"] = {
            "tickets_chat_id": al_chat_edit.text().strip() or "chat97",
            "poll_interval": poll_interval,
        }
        save_config(config)
        messagebox.showinfo("Guardado", "Configuración de Tickets actualizada.")

    btn_guardar = PrimaryPushButton(FluentIcon.SAVE, "Guardar configuración")
    btn_guardar.clicked.connect(guardar_tickets)
    fila_guardar = QHBoxLayout()
    fila_guardar.addStretch(1)
    fila_guardar.addWidget(btn_guardar)
    fila_guardar_widget = QWidget()
    fila_guardar_widget.setLayout(fila_guardar)

    conexion_content = columna(
        texto_informativo("ℹ  El Webhook y el User ID de Bitrix24 se configuran una sola vez en la pestaña Configuración."),
        fila_campo("Chat de tickets (ID):", al_chat_edit),
        fila_campo(
            "Intervalo de sondeo (s):", al_poll_edit,
            ayuda=boton_ayuda(
                "Cuando llegue al chat un mensaje con el formato \"ticket <número> asignado a "
                "[USER=<id>]...\" y el <id> coincida con 'Mi User ID' (pestaña Configuración), la aplicación le "
                "da like automáticamente. No reacciona a ningún otro tipo de mensaje del chat.",
                titulo="Auto-like de tickets",
            ),
        ),
        fila_guardar_widget,
    )
    conexion_card = ctx.seccion_plegable(FluentIcon.LINK, "Conexión Bitrix24", conexion_content, "tickets.conexion")
    layout.addWidget(conexion_card)

    # ---------------- Control del monitor ----------------
    control_card = HeaderCardWidget("▶  Monitor")

    auto_like_status_label = BodyLabel()
    auto_like_status_label.setStyleSheet(f"color: {ACCENT}; font-weight: bold;")
    auto_like_status_var = LabelVar(auto_like_status_label)

    monitor = ctx.register_process(ManagedProcess(
        root=root,
        launch_cmd=lambda: ctx.get_launch_cmd(['--auto-like-tickets']),
    )).bind(auto_like_status_var, on_change=lambda running: (
        btn_al_iniciar.setEnabled(not running),
        btn_al_detener.setEnabled(running),
    ))

    def al_iniciar():
        if monitor.running:
            messagebox.showinfo("Tickets", "El monitor ya está en ejecución.")
            return
        if not config.get("bitrix_webhook_url", "").strip():
            messagebox.showerror("Error", "Configura primero la Webhook URL de Bitrix24 en la pestaña Configuración.")
            return

        monitor.start()
        messagebox.showinfo("Tickets", "Monitor iniciado en segundo plano.\nRevisa 'auto_like_tickets.log' para ver la actividad.")

    def al_detener():
        monitor.stop()
        messagebox.showinfo("Tickets", "Monitor detenido.")

    def _al_autostart():
        # Este monitor se enciende solo al abrir la aplicación -ver
        # ctx.register_autostart() y gui/app.py-, sin diálogos: si falta
        # configuración simplemente no arranca, igual que si el usuario
        # nunca hubiera tocado el botón "Iniciar Monitor".
        if not monitor.running and config.get("bitrix_webhook_url", "").strip():
            monitor.start()

    ctx.register_autostart(_al_autostart)

    btn_al_iniciar = PrimaryPushButton(FluentIcon.PLAY, "Iniciar Monitor")
    btn_al_iniciar.clicked.connect(al_iniciar)

    btn_al_detener = PushButton(FluentIcon.PAUSE, "Detener Monitor")
    btn_al_detener.setEnabled(False)
    btn_al_detener.clicked.connect(al_detener)

    btn_ver_log = crear_visor_log(base_dir / "auto_like_tickets.log", root)

    fila_botones = QHBoxLayout()
    fila_botones.addWidget(btn_al_iniciar)
    fila_botones.addWidget(btn_al_detener)
    fila_botones.addSpacing(10)
    fila_botones.addWidget(auto_like_status_label)
    fila_botones.addStretch(1)
    fila_botones.addWidget(btn_ver_log)
    fila_botones_widget = QWidget()
    fila_botones_widget.setLayout(fila_botones)

    monitor_content = columna(fila_botones_widget, btn_ver_log.panel, margins=(24, 16, 24, 20))
    control_card.viewLayout.addWidget(monitor_content)
    layout.addWidget(control_card)

    layout.addStretch(1)
    return page
