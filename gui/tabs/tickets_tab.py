"""Pestaña "Tickets": configura y controla el monitor de auto-like de
tickets asignados. La lógica del monitor vive en auto_like_tickets.py
(raíz del proyecto), no aquí — este archivo solo construye la pestaña."""
import os
import tkinter as tk
from tkinter import ttk, messagebox

from gui.process_control import ManagedProcess


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

    # Por comodidad, si todavía no se configuró un Webhook propio para
    # Tickets, se sugiere el mismo que ya esté guardado para Cargas VOIP
    # (normalmente es la misma cuenta de Bitrix24).
    if not al_cfg.get("bitrix_webhook_url"):
        al_cfg["bitrix_webhook_url"] = config.get("cargas_voip_config", {}).get("bitrix_webhook_url", "")

    al_conexion_frame = ttk.LabelFrame(tab_tickets, text="🔌  Conexión Bitrix24", padding=15)
    al_conexion_frame.pack(fill="x", padx=10, pady=(15, 10))

    ttk.Label(al_conexion_frame, text="Webhook URL:", font=("Segoe UI", 10, "bold")).grid(row=0, column=0, sticky="w", pady=5)
    al_webhook_var = tk.StringVar(value=al_cfg.get("bitrix_webhook_url", ""))
    ttk.Entry(al_conexion_frame, textvariable=al_webhook_var, width=55).grid(row=0, column=1, columnspan=3, padx=15, pady=5, sticky="we")

    ttk.Label(al_conexion_frame, text="Chat de tickets (ID):", font=("Segoe UI", 10, "bold")).grid(row=1, column=0, sticky="w", pady=5)
    al_chat_var = tk.StringVar(value=al_cfg.get("tickets_chat_id", "chat97"))
    ttk.Entry(al_conexion_frame, textvariable=al_chat_var, width=20).grid(row=1, column=1, padx=15, pady=5, sticky="w")

    ttk.Label(al_conexion_frame, text="Mi User ID:", font=("Segoe UI", 10, "bold")).grid(row=1, column=2, sticky="w", pady=5, padx=(20, 0))
    al_user_var = tk.StringVar(value=str(al_cfg.get("mi_user_id", "13")))
    ttk.Entry(al_conexion_frame, textvariable=al_user_var, width=10).grid(row=1, column=3, padx=15, pady=5, sticky="w")
    al_conexion_frame.columnconfigure(1, weight=1)

    ttk.Label(al_conexion_frame, text="Intervalo de sondeo (s):", font=("Segoe UI", 10, "bold")).grid(row=2, column=0, sticky="w", pady=5)
    al_poll_var = tk.StringVar(value=str(al_cfg.get("poll_interval", 20)))
    ttk.Entry(al_conexion_frame, textvariable=al_poll_var, width=10).grid(row=2, column=1, padx=15, pady=5, sticky="w")

    aviso_tickets = ("Cuando llegue al chat un mensaje con el formato "
                      "\"ticket <número> asignado a [USER=<id>]...\" y el <id> coincida con "
                      "'Mi User ID', la aplicación le da like automáticamente. No reacciona a "
                      "ningún otro tipo de mensaje del chat.")
    ttk.Label(al_conexion_frame, text=aviso_tickets, foreground="#9399b2", wraplength=650,
              justify="left", font=("Segoe UI", 9)).grid(row=3, column=0, columnspan=4, sticky="w", pady=(10, 0))

    def guardar_tickets():
        try:
            poll_interval = int(al_poll_var.get())
        except ValueError:
            messagebox.showerror("Error", "El intervalo de sondeo debe ser un número entero de segundos.")
            return
        if not al_user_var.get().strip():
            messagebox.showerror("Error", "Indica tu User ID de Bitrix24.")
            return

        config["auto_like_tickets_config"] = {
            "bitrix_webhook_url": al_webhook_var.get().strip(),
            "tickets_chat_id": al_chat_var.get().strip() or "chat97",
            "mi_user_id": al_user_var.get().strip(),
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
        if not config.get("auto_like_tickets_config", {}).get("bitrix_webhook_url", "").strip():
            messagebox.showerror("Error", "Configura primero la Webhook URL de Bitrix24 y guarda los cambios.")
            return

        monitor.start()
        messagebox.showinfo("Tickets", "Monitor iniciado en segundo plano.\nRevisa 'auto_like_tickets.log' para ver la actividad.")

    def al_detener():
        monitor.stop()
        messagebox.showinfo("Tickets", "Monitor detenido.")

    def al_ver_log():
        log_path = base_dir / "auto_like_tickets.log"
        if not log_path.exists():
            messagebox.showinfo("Log", "Todavía no se ha generado 'auto_like_tickets.log'.")
            return
        os.startfile(str(log_path))

    al_btn_row = ttk.Frame(al_control_frame)
    al_btn_row.pack(fill="x")

    btn_al_iniciar = ttk.Button(al_btn_row, text="▶  Iniciar Monitor", command=al_iniciar, style="Accent.TButton")
    btn_al_iniciar.pack(side="left", padx=3)

    btn_al_detener = ttk.Button(al_btn_row, text="⏹  Detener Monitor", command=al_detener, state="disabled")
    btn_al_detener.pack(side="left", padx=3)

    ttk.Label(al_btn_row, textvariable=auto_like_status_var, foreground=ACCENT, font=("Segoe UI", 10, "bold")).pack(side="left", padx=15)

    ttk.Button(al_btn_row, text="🗒  Ver log", command=al_ver_log).pack(side="right", padx=3)
