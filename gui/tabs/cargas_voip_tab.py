"""Pestaña "Cargas VOIP": configura y controla el monitor de validación
de cargas (Bitrix24 + OCR). La lógica del monitor en sí vive en
cargas_voip.py (raíz del proyecto), no aquí — este archivo solo construye
la pestaña."""
import os
import tkinter as tk
from tkinter import ttk, filedialog

from core.tesseract_tools import find_tesseract_exe, install_tesseract, has_spanish_traineddata
from gui.process_control import ManagedProcess
from gui import dialogs as messagebox
from gui.dialogs import boton_ayuda, checkbox_modo_prueba
from gui.log_viewer import crear_visor_log
from gui.collapsible import crear_seccion_plegable


def build(notebook, ctx):
    root = ctx.root
    BG = ctx.BG
    ACCENT = ctx.ACCENT
    config = ctx.config
    save_config = ctx.save_config
    base_dir = ctx.base_dir

    tab_cargas = ttk.Frame(notebook)
    notebook.add(tab_cargas, text="   Cargas VOIP   ")

    try:
        import cargas_voip
        cv_cfg = cargas_voip.load_cargas_voip_config()
    except Exception:
        cv_cfg = config.get("cargas_voip_config", {})

    # La pestaña tiene varias secciones y no todas caben sin agrandar la
    # ventana, así que se muestra dentro de un canvas con scrollbar vertical
    # (mismo patrón que el formulario "Agregar proveedor" de Proveedores).
    cv_canvas = tk.Canvas(tab_cargas, bg=BG, highlightthickness=0)
    cv_vscroll = ttk.Scrollbar(tab_cargas, orient="vertical", command=cv_canvas.yview)
    cv_content = ttk.Frame(cv_canvas)
    cv_content_window = cv_canvas.create_window((0, 0), window=cv_content, anchor="nw")
    cv_content.bind("<Configure>", lambda e: cv_canvas.configure(scrollregion=cv_canvas.bbox("all")))
    cv_canvas.bind("<Configure>", lambda e: cv_canvas.itemconfigure(cv_content_window, width=e.width))
    cv_canvas.configure(yscrollcommand=cv_vscroll.set)
    cv_canvas.pack(side="left", fill="both", expand=True)
    cv_vscroll.pack(side="right", fill="y")

    def _cv_on_mousewheel(event):
        cv_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def _cv_bind_mousewheel(_event=None):
        cv_canvas.bind_all("<MouseWheel>", _cv_on_mousewheel)

    def _cv_unbind_mousewheel(_event=None):
        cv_canvas.unbind_all("<MouseWheel>")

    cv_canvas.bind("<Enter>", _cv_bind_mousewheel)
    cv_canvas.bind("<Leave>", _cv_unbind_mousewheel)

    conexion_wrapper, conexion_frame = crear_seccion_plegable(cv_content, "🔌  Conexión Bitrix24")
    conexion_wrapper.pack(fill="x", padx=10, pady=(15, 10))

    ttk.Label(conexion_frame, text="Webhook URL:", font=("Segoe UI", 10, "bold")).grid(row=0, column=0, sticky="w", pady=5)
    cv_webhook_var = tk.StringVar(value=cv_cfg.get("bitrix_webhook_url", ""))
    ttk.Entry(conexion_frame, textvariable=cv_webhook_var, width=55).grid(row=0, column=1, columnspan=3, padx=15, pady=5, sticky="we")

    ttk.Label(conexion_frame, text="Chat ID:", font=("Segoe UI", 10, "bold")).grid(row=1, column=0, sticky="w", pady=5)
    cv_chat_var = tk.StringVar(value=cv_cfg.get("chat_id", "chat135"))
    ttk.Entry(conexion_frame, textvariable=cv_chat_var, width=20).grid(row=1, column=1, padx=15, pady=5, sticky="w")
    conexion_frame.columnconfigure(1, weight=1)

    fila_estado_sheet = ttk.Frame(conexion_frame)
    fila_estado_sheet.grid(row=2, column=0, sticky="w", pady=5)
    boton_ayuda(
        fila_estado_sheet,
        "Aquí se guarda hasta qué mensaje ya se revisó, compartido entre las 3 PC que "
        "usan este monitor por turnos -así ninguna reprocesa lo que otra ya validó-. "
        "Usa las mismas credenciales de Google ya configuradas en la pestaña Configuración.",
        titulo="Hoja de estado compartido",
    ).pack(side="left", padx=(0, 6))
    ttk.Label(fila_estado_sheet, text="Hoja de estado compartido (URL):",
              font=("Segoe UI", 10, "bold")).pack(side="left")
    # Si "import cargas_voip" falló arriba (ver el try/except), no se puede
    # referenciar cargas_voip.ESTADO_SHEET_URL_DEFAULT -por eso el mismo
    # valor por defecto queda duplicado acá como string literal-.
    cv_estado_sheet_var = tk.StringVar(value=cv_cfg.get(
        "estado_sheet_url",
        "https://docs.google.com/spreadsheets/d/1ql2y9RBBS4aelGIIawJNielWYo_BUwxtNRCdvVy_gac/edit?gid=0"))
    ttk.Entry(conexion_frame, textvariable=cv_estado_sheet_var, width=55).grid(
        row=2, column=1, columnspan=3, padx=15, pady=5, sticky="we")

    validacion_wrapper, validacion_frame = crear_seccion_plegable(cv_content, "✅  Validación")
    validacion_wrapper.pack(fill="x", padx=10, pady=(0, 10))

    ttk.Label(validacion_frame, text="Intervalo de sondeo (s):", font=("Segoe UI", 10, "bold")).grid(row=0, column=0, sticky="w", pady=5)
    cv_poll_var = tk.StringVar(value=str(cv_cfg.get("poll_interval", 20)))
    ttk.Entry(validacion_frame, textvariable=cv_poll_var, width=10).grid(row=0, column=1, padx=15, pady=5, sticky="w")

    ttk.Label(validacion_frame, text="Tolerancia monto USD (%):", font=("Segoe UI", 10, "bold")).grid(row=0, column=2, sticky="w", pady=5, padx=(20, 0))
    cv_tol_var = tk.StringVar(value=str(cv_cfg.get("tolerancia_pct", 0.5)))
    ttk.Entry(validacion_frame, textvariable=cv_tol_var, width=10).grid(row=0, column=3, padx=15, pady=5, sticky="w")

    ttk.Label(validacion_frame, text="Tolerancia TRM (%):", font=("Segoe UI", 10, "bold")).grid(row=1, column=0, sticky="w", pady=5)
    cv_tol_trm_var = tk.StringVar(value=str(cv_cfg.get("tolerancia_trm_pct", 0.1)))
    ttk.Entry(validacion_frame, textvariable=cv_tol_trm_var, width=10).grid(row=1, column=1, padx=15, pady=5, sticky="w")

    fila_lookback = ttk.Frame(validacion_frame)
    fila_lookback.grid(row=1, column=2, sticky="w", pady=5, padx=(20, 0))
    boton_ayuda(
        fila_lookback,
        "En cada ciclo, además de los mensajes nuevos, revisa esta cantidad de mensajes anteriores "
        "al último ya visto -por si alguno se quedó sin confirmar- y lo reprocesa si no consta como "
        "ya confirmado (ni en el registro local de esta PC ni en el de las otras, compartido en la "
        "misma hoja de Sheets del estado).",
        titulo="Ventana de reintento",
    ).pack(side="left", padx=(0, 6))
    ttk.Label(fila_lookback, text="Ventana de reintento (mensajes atrás):",
              font=("Segoe UI", 10, "bold")).pack(side="left")
    cv_lookback_var = tk.StringVar(value=str(cv_cfg.get("lookback_n", 10)))
    ttk.Entry(validacion_frame, textvariable=cv_lookback_var, width=10).grid(row=1, column=3, padx=15, pady=5, sticky="w")

    cv_dry_run_var = tk.BooleanVar(value=bool(cv_cfg.get("dry_run", True)))
    fila_dry_run = ttk.Frame(validacion_frame)
    fila_dry_run.grid(row=2, column=0, columnspan=4, sticky="w", pady=(10, 0))
    checkbox_modo_prueba(fila_dry_run, cv_dry_run_var).pack(side="left")
    boton_ayuda(
        fila_dry_run,
        "Detecta y registra qué haría en el log, sin escribir nada en el chat de Bitrix24 ni modificar "
        "el CSV de auditoría ni el estado compartido (Sheets). Actívalo para probar cambios sin que el "
        "equipo vea mensajes de prueba ni se entere si algo sale mal.",
        titulo="Modo de prueba (dry-run)",
    ).pack(side="left", padx=(6, 0))

    ocr_wrapper, ocr_frame = crear_seccion_plegable(cv_content, "🔎  OCR (Tesseract)")
    # OCR no se empaqueta aquí: se deja para el final del archivo para que
    # quede de última, después de Monitor (ver comentario junto a Monitor).

    def seleccionar_tesseract():
        path = filedialog.askopenfilename(
            title="Seleccionar tesseract.exe",
            filetypes=[("Ejecutable", "*.exe"), ("Todos", "*.*")]
        )
        if path:
            cv_tesseract_var.set(path)
            actualizar_estado_tesseract()

    row_tess = ttk.Frame(ocr_frame)
    row_tess.pack(fill="x")
    boton_ayuda(
        row_tess,
        "El OCR es obligatorio para validar las capturas de carga: Tesseract debe estar instalado "
        "(usa el botón de abajo para descargarlo) antes de poder iniciar el monitor. El registro "
        "detallado de cada lectura queda siempre activado en 'cargas_voip.log'.",
        titulo="OCR (Tesseract)",
    ).pack(side="left", padx=(0, 6))
    ttk.Label(row_tess, text="Ruta tesseract.exe:", font=("Segoe UI", 10, "bold")).pack(side="left", padx=(0, 8))
    cv_tesseract_var = tk.StringVar(value=cv_cfg.get("tesseract_cmd", ""))
    ttk.Entry(row_tess, textvariable=cv_tesseract_var).pack(side="left", expand=True, fill="x", padx=(0, 10))
    ttk.Button(row_tess, text="Examinar", command=seleccionar_tesseract).pack(side="left", padx=3)

    def actualizar_estado_tesseract():
        found = find_tesseract_exe(cv_tesseract_var.get().strip())
        if found:
            cv_tesseract_status_var.set(f"✓  Detectado: {found}")
            if not cv_tesseract_var.get().strip():
                cv_tesseract_var.set(found)
        else:
            cv_tesseract_status_var.set("✗  No se detectó Tesseract OCR en este equipo.")

    def descargar_instalar_tesseract():
        respuesta = messagebox.askyesno(
            "Descargar e instalar Tesseract",
            "Esto descargará el instalador oficial más reciente de Tesseract OCR "
            "desde GitHub (tesseract-ocr/tesseract) y lo ejecutará.\n\n"
            "Windows pedirá permiso de administrador porque se instala en "
            "'Program Files'. Acepta ese aviso para continuar.\n\n¿Continuar?"
        )
        if not respuesta:
            return

        btn_descargar_tesseract.config(state="disabled", text="Instalando...")
        cv_tesseract_status_var.set("Consultando última versión disponible...")

        def progreso(msg):
            root.after(0, lambda: cv_tesseract_status_var.set(msg))

        def tarea():
            try:
                version, exe_path = install_tesseract(progress_cb=progreso)

                def terminar_ok():
                    if exe_path:
                        cv_tesseract_var.set(exe_path)
                        config.setdefault("cargas_voip_config", {})["tesseract_cmd"] = exe_path
                        save_config(config)
                        actualizar_estado_tesseract()
                        messagebox.showinfo(
                            "Tesseract instalado",
                            f"Tesseract {version} instalado correctamente.\nRuta guardada: {exe_path}"
                        )
                    else:
                        actualizar_estado_tesseract()
                        messagebox.showwarning(
                            "Revisar instalación",
                            "Se ejecutó el instalador pero no se pudo confirmar la instalación. "
                            "Si se abrió su asistente, complétalo y vuelve a intentar."
                        )
                root.after(0, terminar_ok)
            except Exception as e:
                root.after(0, lambda: (
                    actualizar_estado_tesseract(),
                    messagebox.showerror("Error", f"No se pudo instalar Tesseract:\n{e}")
                ))
            finally:
                root.after(0, lambda: btn_descargar_tesseract.config(state="normal", text="⬇  Descargar e instalar Tesseract"))

        import threading
        threading.Thread(target=tarea, daemon=True).start()

    row_tess_estado = ttk.Frame(ocr_frame)
    row_tess_estado.pack(fill="x", pady=(8, 0))
    cv_tesseract_status_var = tk.StringVar(value="Comprobando...")
    ttk.Label(row_tess_estado, textvariable=cv_tesseract_status_var, foreground="#9399b2",
              font=("Segoe UI", 9)).pack(side="left")
    btn_descargar_tesseract = ttk.Button(
        row_tess_estado, text="⬇  Descargar e instalar Tesseract",
        command=descargar_instalar_tesseract, style="Accent.TButton"
    )
    btn_descargar_tesseract.pack(side="right", padx=3)
    actualizar_estado_tesseract()

    def guardar_cargas_voip():
        try:
            poll_interval = int(cv_poll_var.get())
        except ValueError:
            messagebox.showerror("Error", "El intervalo de sondeo debe ser un número entero de segundos.")
            return
        try:
            tolerancia_pct = float(cv_tol_var.get())
            tolerancia_trm_pct = float(cv_tol_trm_var.get())
        except ValueError:
            messagebox.showerror("Error", "Las tolerancias deben ser números.")
            return
        try:
            lookback_n = int(cv_lookback_var.get())
        except ValueError:
            messagebox.showerror("Error", "La ventana de reintento debe ser un número entero de mensajes.")
            return

        config["cargas_voip_config"] = {
            "bitrix_webhook_url": cv_webhook_var.get().strip(),
            "chat_id": cv_chat_var.get().strip() or "chat135",
            "poll_interval": poll_interval,
            "tolerancia_pct": tolerancia_pct,
            "tolerancia_trm_pct": tolerancia_trm_pct,
            "tesseract_cmd": cv_tesseract_var.get().strip(),
            "debug_ocr": True,
            "estado_sheet_url": cv_estado_sheet_var.get().strip(),
            "lookback_n": lookback_n,
            "dry_run": bool(cv_dry_run_var.get()),
        }
        save_config(config)
        messagebox.showinfo("Guardado", "Configuración de Cargas VOIP actualizada.")

    # ---------------- Control del monitor ----------------
    cv_control_frame = ttk.LabelFrame(cv_content, text="▶  Monitor", padding=15)
    cv_control_frame.pack(fill="x", padx=10, pady=(0, 10))

    cargas_voip_status_var = tk.StringVar()
    monitor = ctx.register_process(ManagedProcess(
        root=root,
        launch_cmd=lambda: ctx.get_launch_cmd(['--cargas-voip']),
    )).bind(cargas_voip_status_var, on_change=lambda running: (
        btn_cv_iniciar.config(state=tk.DISABLED if running else tk.NORMAL),
        btn_cv_detener.config(state=tk.NORMAL if running else tk.DISABLED),
    ))

    def cv_iniciar():
        if monitor.running:
            messagebox.showinfo("Cargas VOIP", "El monitor ya está en ejecución.")
            return
        if not config.get("cargas_voip_config", {}).get("bitrix_webhook_url", "").strip():
            messagebox.showerror("Error", "Configura primero la Webhook URL de Bitrix24 y guarda los cambios.")
            return
        tesseract_exe = find_tesseract_exe(config.get("cargas_voip_config", {}).get("tesseract_cmd", ""))
        if not tesseract_exe:
            messagebox.showerror(
                "Tesseract requerido",
                "El OCR es obligatorio para validar las capturas: instala Tesseract con el botón "
                "'⬇  Descargar e instalar Tesseract' antes de iniciar el monitor."
            )
            return
        if not has_spanish_traineddata(tesseract_exe):
            messagebox.showerror(
                "Falta el paquete de idioma español",
                "Tesseract está instalado pero sin el paquete de español (spa.traineddata). "
                "El OCR lee los montos en silencio solo con inglés, lo que puede generar lecturas "
                "incorrectas. Usa el botón '⬇  Descargar e instalar Tesseract' para instalarlo "
                "antes de iniciar el monitor."
            )
            return

        monitor.start()
        messagebox.showinfo("Cargas VOIP", "Monitor iniciado en segundo plano.\nRevisa 'cargas_voip.log' para ver la actividad.")

    def cv_detener():
        monitor.stop()
        messagebox.showinfo("Cargas VOIP", "Monitor detenido.")

    def cv_ver_registro():
        csv_path = base_dir / "registro_cargas.csv"
        if not csv_path.exists():
            messagebox.showinfo("Registro", "Todavía no se ha generado 'registro_cargas.csv'.")
            return
        os.startfile(str(csv_path))

    cv_btn_row = ttk.Frame(cv_control_frame)
    cv_btn_row.pack(fill="x")

    btn_cv_iniciar = ttk.Button(cv_btn_row, text="▶  Iniciar Monitor", command=cv_iniciar, style="Accent.TButton")
    btn_cv_iniciar.pack(side="left", padx=3)

    btn_cv_detener = ttk.Button(cv_btn_row, text="⏹  Detener Monitor", command=cv_detener, state="disabled")
    btn_cv_detener.pack(side="left", padx=3)

    ttk.Label(cv_btn_row, textvariable=cargas_voip_status_var, foreground=ACCENT, font=("Segoe UI", 10, "bold")).pack(side="left", padx=15)

    ttk.Button(cv_btn_row, text="📄  Ver registro (CSV)", command=cv_ver_registro).pack(side="right", padx=3)
    crear_visor_log(cv_btn_row, cv_control_frame, base_dir / "cargas_voip.log", root).pack(side="right", padx=3)

    # OCR se empaqueta hasta aquí (después de Monitor) para que el orden
    # visual quede: Conexión Bitrix24, Validación, Monitor, OCR (Tesseract).
    ocr_wrapper.pack(fill="x", padx=10, pady=(0, 10))

    # Botón de guardar al final, afuera de las secciones plegables -aplica
    # a la configuración completa de la pestaña, no solo a la de OCR-.
    ttk.Button(cv_content, text="💾  Guardar configuración", command=guardar_cargas_voip, style="Accent.TButton").pack(
        anchor="e", padx=10, pady=(0, 15)
    )
