"""Pestaña "Notificaciones": configura y controla el monitor de avisos de
escritorio para Canales Abiertos (chats nuevos / mensajes nuevos). La
lógica del monitor vive en notificaciones_bitrix.py (raíz del proyecto),
no aquí — este archivo solo construye la pestaña."""
import tkinter as tk
from tkinter import ttk

from gui.process_control import ManagedProcess
from gui import dialogs as messagebox
from gui.dialogs import boton_ayuda
from gui.log_viewer import crear_visor_log
from gui.collapsible import crear_seccion_plegable


def build(notebook, ctx):
    root = ctx.root
    ACCENT = ctx.ACCENT
    config = ctx.config
    save_config = ctx.save_config
    base_dir = ctx.base_dir

    tab_notif = ttk.Frame(notebook)
    notebook.add(tab_notif, text="   Notificaciones   ")

    try:
        import notificaciones_bitrix
        nf_cfg = notificaciones_bitrix.load_notificaciones_config()
    except Exception:
        nf_cfg = config.get("notificaciones_config", {})

    # Por comodidad, si todavía no se configuró un Webhook propio para
    # Notificaciones, se sugiere el mismo que ya esté guardado para
    # Cargas VOIP (normalmente es la misma cuenta de Bitrix24).
    if not nf_cfg.get("bitrix_webhook_url"):
        nf_cfg["bitrix_webhook_url"] = config.get("cargas_voip_config", {}).get("bitrix_webhook_url", "")

    nf_conexion_wrapper, nf_conexion_frame = crear_seccion_plegable(tab_notif, "🔌  Conexión Bitrix24")
    nf_conexion_wrapper.pack(fill="x", padx=10, pady=(15, 10))

    ttk.Label(nf_conexion_frame, text="Webhook URL:", font=("Segoe UI", 10, "bold")).grid(row=0, column=0, sticky="w", pady=5)
    nf_webhook_var = tk.StringVar(value=nf_cfg.get("bitrix_webhook_url", ""))
    ttk.Entry(nf_conexion_frame, textvariable=nf_webhook_var, width=55).grid(row=0, column=1, columnspan=3, padx=15, pady=5, sticky="we")

    ttk.Label(nf_conexion_frame, text="Mi User ID:", font=("Segoe UI", 10, "bold")).grid(row=1, column=0, sticky="w", pady=5)
    nf_user_var = tk.StringVar(value=str(nf_cfg.get("mi_user_id", "13")))
    ttk.Entry(nf_conexion_frame, textvariable=nf_user_var, width=10).grid(row=1, column=1, padx=15, pady=5, sticky="w")
    nf_conexion_frame.columnconfigure(1, weight=1)

    fila_poll = ttk.Frame(nf_conexion_frame)
    fila_poll.grid(row=2, column=0, sticky="w", pady=5)
    boton_ayuda(
        fila_poll,
        "Revisa periódicamente los chats de \"Canales abiertos\" de Bitrix24 y muestra una "
        "notificación de Windows (como las de WhatsApp) cuando aparece un chat nuevo o llega "
        "un mensaje nuevo. No se notifican los mensajes que tú mismo envíes (según 'Mi User ID').",
        titulo="Notificaciones de Canales abiertos",
    ).pack(side="left", padx=(0, 6))
    ttk.Label(fila_poll, text="Intervalo de sondeo (s):", font=("Segoe UI", 10, "bold")).pack(side="left")
    nf_poll_var = tk.StringVar(value=str(nf_cfg.get("poll_interval", 15)))
    ttk.Entry(nf_conexion_frame, textvariable=nf_poll_var, width=10).grid(row=2, column=1, padx=15, pady=5, sticky="w")

    def guardar_notificaciones():
        try:
            poll_interval = int(nf_poll_var.get())
        except ValueError:
            messagebox.showerror("Error", "El intervalo de sondeo debe ser un número entero de segundos.")
            return
        if not nf_user_var.get().strip():
            messagebox.showerror("Error", "Indica tu User ID de Bitrix24.")
            return

        config["notificaciones_config"] = {
            "bitrix_webhook_url": nf_webhook_var.get().strip(),
            "mi_user_id": nf_user_var.get().strip(),
            "poll_interval": poll_interval,
        }
        save_config(config)
        messagebox.showinfo("Guardado", "Configuración de Notificaciones actualizada.")

    def probar_notificacion():
        try:
            from winotify import Notification
            Notification(
                app_id="Saldos Scheduler - Canales abiertos",
                title="Notificación de prueba",
                msg="Si ves esto en la bandeja de Windows, las notificaciones funcionan.",
                icon=str(base_dir / "icono.ico") if (base_dir / "icono.ico").exists() else "",
                duration="long",
            ).show()
        except Exception as e:
            messagebox.showerror("Error", f"No se pudo mostrar la notificación de prueba:\n{e}")

    botones_frame = ttk.Frame(nf_conexion_frame)
    botones_frame.grid(row=4, column=0, columnspan=4, pady=(15, 0), sticky="e")
    ttk.Button(botones_frame, text="🔔  Probar notificación", command=probar_notificacion).pack(side="right", padx=(8, 0))
    ttk.Button(botones_frame, text="💾  Guardar configuración", command=guardar_notificaciones, style="Accent.TButton").pack(side="right")

    # ---------------- Control del monitor ----------------
    nf_control_frame = ttk.LabelFrame(tab_notif, text="▶  Monitor", padding=15)
    nf_control_frame.pack(fill="x", padx=10, pady=(0, 10))

    notif_status_var = tk.StringVar()
    monitor = ctx.register_process(ManagedProcess(
        root=root,
        launch_cmd=lambda: ctx.get_launch_cmd(['--notificaciones']),
    )).bind(notif_status_var, on_change=lambda running: (
        btn_nf_iniciar.config(state=tk.DISABLED if running else tk.NORMAL),
        btn_nf_detener.config(state=tk.NORMAL if running else tk.DISABLED),
    ))

    def nf_iniciar():
        if monitor.running:
            messagebox.showinfo("Notificaciones", "El monitor ya está en ejecución.")
            return
        if not config.get("notificaciones_config", {}).get("bitrix_webhook_url", "").strip():
            messagebox.showerror("Error", "Configura primero la Webhook URL de Bitrix24 y guarda los cambios.")
            return

        monitor.start()
        messagebox.showinfo("Notificaciones", "Monitor iniciado en segundo plano.\nRevisa 'notificaciones_bitrix.log' para ver la actividad.")

    def nf_detener():
        monitor.stop()
        messagebox.showinfo("Notificaciones", "Monitor detenido.")

    nf_btn_row = ttk.Frame(nf_control_frame)
    nf_btn_row.pack(fill="x")

    btn_nf_iniciar = ttk.Button(nf_btn_row, text="▶  Iniciar Monitor", command=nf_iniciar, style="Accent.TButton")
    btn_nf_iniciar.pack(side="left", padx=3)

    btn_nf_detener = ttk.Button(nf_btn_row, text="⏹  Detener Monitor", command=nf_detener, state="disabled")
    btn_nf_detener.pack(side="left", padx=3)

    ttk.Label(nf_btn_row, textvariable=notif_status_var, foreground=ACCENT, font=("Segoe UI", 10, "bold")).pack(side="left", padx=15)

    crear_visor_log(nf_btn_row, nf_control_frame, base_dir / "notificaciones_bitrix.log", root).pack(side="right", padx=3)
