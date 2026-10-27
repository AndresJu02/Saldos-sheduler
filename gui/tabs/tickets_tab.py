"""Pestaña "Tickets": configura y controla el monitor de auto-like de
tickets asignados. La lógica del monitor vive en auto_like_tickets.py
(raíz del proyecto), no aquí — este archivo solo construye la pestaña."""
import tkinter as tk
from tkinter import ttk

from gui.process_control import ManagedProcess
from gui import dialogs as messagebox
from gui.dialogs import boton_ayuda
from gui.log_viewer import crear_visor_log


def build(notebook, ctx):
    root = ctx.root
    ACCENT = ctx.ACCENT
    config = ctx.config
    save_config = ctx.save_config
    base_dir = ctx.base_dir

    tab_tickets = ttk.Frame(notebook)
    notebook.add(tab_tickets, text="   Tickets   ")

    try:
        import auto_like_tickets
        al_cfg = auto_like_tickets.load_auto_like_config()
    except Exception:
        al_cfg = config.get("auto_like_tickets_config", {})

    al_conexion_wrapper, al_conexion_frame = ctx.seccion_plegable(tab_tickets, "🔌  Conexión Bitrix24", "tickets.conexion")
    al_conexion_wrapper.pack(fill="x", padx=10, pady=(15, 10))

    ttk.Label(
        al_conexion_frame,
        text="ℹ  El Webhook y el User ID de Bitrix24 se configuran una sola vez en la pestaña Configuración.",
        foreground="#9399b2", font=("Segoe UI", 9)
    ).grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 5))

    ttk.Label(al_conexion_frame, text="Chat de tickets (ID):", font=("Segoe UI", 10, "bold")).grid(row=1, column=0, sticky="w", pady=5)
    al_chat_var = tk.StringVar(value=al_cfg.get("tickets_chat_id", "chat97"))
    ttk.Entry(al_conexion_frame, textvariable=al_chat_var, width=20).grid(row=1, column=1, padx=15, pady=5, sticky="w")
    al_conexion_frame.columnconfigure(1, weight=1)

    fila_poll = ttk.Frame(al_conexion_frame)
    fila_poll.grid(row=2, column=0, sticky="w", pady=5)
    boton_ayuda(
        fila_poll,
        "Cuando llegue al chat un mensaje con el formato \"ticket <número> asignado a "
        "[USER=<id>]...\" y el <id> coincida con 'Mi User ID' (pestaña Configuración), la aplicación le da like "
        "automáticamente. No reacciona a ningún otro tipo de mensaje del chat.",
        titulo="Auto-like de tickets",
    ).pack(side="left", padx=(0, 6))
    ttk.Label(fila_poll, text="Intervalo de sondeo (s):", font=("Segoe UI", 10, "bold")).pack(side="left")
    al_poll_var = tk.StringVar(value=str(al_cfg.get("poll_interval", 20)))
    ttk.Entry(al_conexion_frame, textvariable=al_poll_var, width=10).grid(row=2, column=1, padx=15, pady=5, sticky="w")

    def guardar_tickets():
        try:
            poll_interval = int(al_poll_var.get())
        except ValueError:
            messagebox.showerror("Error", "El intervalo de sondeo debe ser un número entero de segundos.")
            return

        config["auto_like_tickets_config"] = {
            "tickets_chat_id": al_chat_var.get().strip() or "chat97",
            "poll_interval": poll_interval,
        }
        save_config(config)
        messagebox.showinfo("Guardado", "Configuración de Tickets actualizada.")

    ttk.Button(al_conexion_frame, text="💾  Guardar configuración", command=guardar_tickets, style="Accent.TButton").grid(
        row=4, column=3, pady=(15, 0), sticky="e"
    )

    # ---------------- Control del monitor ----------------
    al_control_frame = ttk.LabelFrame(tab_tickets, text="▶  Monitor", padding=15)
    al_control_frame.pack(fill="x", padx=10, pady=(0, 10))

    auto_like_status_var = tk.StringVar()
    monitor = ctx.register_process(ManagedProcess(
        root=root,
        launch_cmd=lambda: ctx.get_launch_cmd(['--auto-like-tickets']),
    )).bind(auto_like_status_var, on_change=lambda running: (
        btn_al_iniciar.config(state=tk.DISABLED if running else tk.NORMAL),
        btn_al_detener.config(state=tk.NORMAL if running else tk.DISABLED),
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

    al_btn_row = ttk.Frame(al_control_frame)
    al_btn_row.pack(fill="x")

    btn_al_iniciar = ttk.Button(al_btn_row, text="▶  Iniciar Monitor", command=al_iniciar, style="Accent.TButton")
    btn_al_iniciar.pack(side="left", padx=3)

    btn_al_detener = ttk.Button(al_btn_row, text="⏹  Detener Monitor", command=al_detener, state="disabled")
    btn_al_detener.pack(side="left", padx=3)

    ttk.Label(al_btn_row, textvariable=auto_like_status_var, foreground=ACCENT, font=("Segoe UI", 10, "bold")).pack(side="left", padx=15)

    crear_visor_log(al_btn_row, al_control_frame, base_dir / "auto_like_tickets.log", root).pack(side="right", padx=3)
