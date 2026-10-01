"""Pestaña "Renovación DIDWW": configura y controla el monitor que
renueva automáticamente líneas DIDWW cuando alguien las pide "Renovar"
en el chat de Bitrix24. La lógica del monitor vive en
didww_renovacion.py (raíz del proyecto), no aquí — este archivo solo
construye la pestaña."""
import tkinter as tk
from tkinter import ttk

from gui.process_control import ManagedProcess
from gui import dialogs as messagebox
from gui.dialogs import boton_ayuda, checkbox_modo_prueba
from gui.log_viewer import crear_visor_log
from gui.collapsible import crear_seccion_plegable


def build(notebook, ctx):
    root = ctx.root
    ACCENT = ctx.ACCENT
    config = ctx.config
    save_config = ctx.save_config
    base_dir = ctx.base_dir

    tab_didww = ttk.Frame(notebook)
    notebook.add(tab_didww, text="   Renovación DIDWW   ")

    try:
        import didww_renovacion
        dw_cfg = didww_renovacion.load_didww_renovacion_config()
    except Exception:
        dw_cfg = config.get("didww_renovacion_config", {})

    # Por comodidad, si todavía no se configuró un Webhook propio para
    # este monitor, se sugiere el mismo que ya esté guardado para Cargas
    # VOIP o Tickets (normalmente es la misma cuenta de Bitrix24).
    if not dw_cfg.get("bitrix_webhook_url"):
        dw_cfg["bitrix_webhook_url"] = (
            config.get("cargas_voip_config", {}).get("bitrix_webhook_url", "")
            or config.get("auto_like_tickets_config", {}).get("bitrix_webhook_url", "")
        )

    conexion_wrapper, conexion_frame = crear_seccion_plegable(tab_didww, "🔌  Conexión Bitrix24")
    conexion_wrapper.pack(fill="x", padx=10, pady=(15, 10))

    ttk.Label(conexion_frame, text="Webhook URL:", font=("Segoe UI", 10, "bold")).grid(row=0, column=0, sticky="w", pady=5)
    dw_webhook_var = tk.StringVar(value=dw_cfg.get("bitrix_webhook_url", ""))
    ttk.Entry(conexion_frame, textvariable=dw_webhook_var, width=55).grid(row=0, column=1, columnspan=3, padx=15, pady=5, sticky="we")

    fila_chat_dw = ttk.Frame(conexion_frame)
    fila_chat_dw.grid(row=1, column=0, sticky="w", pady=5)
    boton_ayuda(
        fila_chat_dw,
        "La API Key de DIDWW no se configura aquí: se reutiliza la misma que ya está guardada "
        "en la pestaña Proveedores → DIDWW → Configurar.",
        titulo="API Key de DIDWW",
    ).pack(side="left", padx=(0, 6))
    ttk.Label(fila_chat_dw, text="Chat de renovaciones (ID):", font=("Segoe UI", 10, "bold")).pack(side="left")
    dw_chat_var = tk.StringVar(value=dw_cfg.get("chat_id", ""))
    ttk.Entry(conexion_frame, textvariable=dw_chat_var, width=20).grid(row=1, column=1, padx=15, pady=5, sticky="w")

    ttk.Label(conexion_frame, text="Intervalo de sondeo (s):", font=("Segoe UI", 10, "bold")).grid(row=1, column=2, sticky="w", pady=5, padx=(20, 0))
    dw_poll_var = tk.StringVar(value=str(dw_cfg.get("poll_interval", 30)))
    ttk.Entry(conexion_frame, textvariable=dw_poll_var, width=10).grid(row=1, column=3, padx=15, pady=5, sticky="w")
    conexion_frame.columnconfigure(1, weight=1)

    dw_dry_run_var = tk.BooleanVar(value=bool(dw_cfg.get("dry_run", True)))
    fila_dry_run_dw = ttk.Frame(conexion_frame)
    fila_dry_run_dw.grid(row=3, column=0, columnspan=4, sticky="w", pady=(10, 0))
    checkbox_modo_prueba(fila_dry_run_dw, dw_dry_run_var).pack(side="left")
    boton_ayuda(
        fila_dry_run_dw,
        "Detecta y registra qué haría en el log, sin modificar nada en DIDWW y SIN escribir nada en el "
        "chat de Bitrix24.",
        titulo="Modo de prueba (dry-run)",
    ).pack(side="left", padx=(6, 0))



    def guardar_didww():
        try:
            poll_interval = int(dw_poll_var.get())
        except ValueError:
            messagebox.showerror("Error", "El intervalo de sondeo debe ser un número entero de segundos.")
            return
        if not dw_chat_var.get().strip():
            messagebox.showerror("Error", "Indica el ID del chat de renovaciones de Bitrix24.")
            return

        config["didww_renovacion_config"] = {
            "bitrix_webhook_url": dw_webhook_var.get().strip(),
            "chat_id": dw_chat_var.get().strip(),
            "poll_interval": poll_interval,
            "dry_run": bool(dw_dry_run_var.get()),
        }
        save_config(config)
        messagebox.showinfo("Guardado", "Configuración de Renovación DIDWW actualizada.")

    ttk.Button(conexion_frame, text="💾  Guardar configuración", command=guardar_didww, style="Accent.TButton").grid(
        row=4, column=3, pady=(15, 0), sticky="e"
    )

    # ---------------- Control del monitor ----------------
    dw_control_frame = ttk.LabelFrame(tab_didww, text="▶  Monitor", padding=15)
    dw_control_frame.pack(fill="x", padx=10, pady=(0, 10))

    didww_renov_status_var = tk.StringVar()
    monitor = ctx.register_process(ManagedProcess(
        root=root,
        launch_cmd=lambda: ctx.get_launch_cmd(['--didww-renovacion']),
    )).bind(didww_renov_status_var, on_change=lambda running: (
        btn_dw_iniciar.config(state=tk.DISABLED if running else tk.NORMAL),
        btn_dw_detener.config(state=tk.NORMAL if running else tk.DISABLED),
    ))

    def dw_iniciar():
        if monitor.running:
            messagebox.showinfo("Renovación DIDWW", "El monitor ya está en ejecución.")
            return
        if not config.get("didww_renovacion_config", {}).get("bitrix_webhook_url", "").strip():
            messagebox.showerror("Error", "Configura primero la Webhook URL de Bitrix24 y guarda los cambios.")
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

    dw_btn_row = ttk.Frame(dw_control_frame)
    dw_btn_row.pack(fill="x")

    btn_dw_iniciar = ttk.Button(dw_btn_row, text="▶  Iniciar Monitor", command=dw_iniciar, style="Accent.TButton")
    btn_dw_iniciar.pack(side="left", padx=3)

    btn_dw_detener = ttk.Button(dw_btn_row, text="⏹  Detener Monitor", command=dw_detener, state="disabled")
    btn_dw_detener.pack(side="left", padx=3)

    ttk.Label(dw_btn_row, textvariable=didww_renov_status_var, foreground=ACCENT, font=("Segoe UI", 10, "bold")).pack(side="left", padx=15)

    crear_visor_log(dw_btn_row, dw_control_frame, base_dir / "didww_renovacion.log", root).pack(side="right", padx=3)
