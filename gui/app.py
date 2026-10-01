"""
Ventana principal: tema, notebook y barra inferior (control del
Scheduler de saldos). Cada pestaña del notebook vive en su propio
archivo bajo gui/tabs/ — este módulo solo las ensambla.

Para agregar una pestaña nueva:
  1. Crear gui/tabs/mi_modulo.py con una función build(notebook, ctx).
  2. Importarla aquí y agregar una línea `mi_modulo.build(notebook, ctx)`
     junto a las demás, en el orden en que quieras que aparezca.
  3. Configuración siempre debe agregarse de última (queda a la derecha).
"""
import tkinter as tk
from tkinter import ttk
from pathlib import Path

from gui import dialogs as messagebox

from remote_lock import verificar_bloqueo
from core.config import load_config, save_config
from core.launch import get_launch_cmd
from core.paths import BASE_DIR

from gui.theme import apply_dark_theme, apply_dark_titlebar
from gui.context import AppContext
from gui.process_control import ManagedProcess
from gui.tabs import proveedores, cargas_voip_tab, tickets_tab, didww_renovacion_tab, tienda_did_tab, configuracion


def run_gui():
    root = tk.Tk()
    root.title("Saldos Scheduler")
    root.geometry("835x600")
    root.minsize(850, 500)
    root.configure(bg="#1e1e2e")

    # Centrar ventana
    root.update_idletasks()
    w = root.winfo_width()
    h = root.winfo_height()
    sw = root.winfo_screenwidth()
    sh = root.winfo_screenheight()
    x = (sw - w) // 2
    y = (sh - h) // 2
    root.geometry(f"+{x}+{y}")

    apply_dark_titlebar(root)
    colors = apply_dark_theme(root)

    config = load_config()

    ctx = AppContext(
        root=root,
        base_dir=BASE_DIR,
        config=config,
        save_config=save_config,
        get_launch_cmd=get_launch_cmd,
        **colors,
    )

    def verificar_y_avisar():
        if verificar_bloqueo():
            messagebox.showwarning(
                "Aplicación bloqueada",
                "⚠️  Esta aplicación ha sido bloqueada por el administrador.\n\n"
                "Contacte al soporte para más información."
            )
    root.after(150, verificar_y_avisar)

    # ====================== NOTEBOOK ======================
    # Se crea (y se le agregan las pestañas) antes de mostrarlo, pero el
    # .pack() se deja para el final -después de empacar la barra inferior-
    # porque el orden de los .pack() es lo que decide qué se encoge primero
    # si la ventana se hace más chica: lo empacado primero (bottom, con
    # tamaño fijo) se respeta, y lo empacado después (notebook, expand=True)
    # es lo que cede espacio. Empacar el notebook antes hacía que los
    # botones de Iniciar/Detener/Ejecutar/Salir desaparecieran al achicar
    # la ventana.
    notebook = ttk.Notebook(root)

    proveedores.build(notebook, ctx)
    cargas_voip_tab.build(notebook, ctx)
    tickets_tab.build(notebook, ctx)
    didww_renovacion_tab.build(notebook, ctx)
    tienda_did_tab.build(notebook, ctx)
    configuracion.build(notebook, ctx)  # siempre de última: queda más a la derecha

    # ====================== BARRA INFERIOR (Scheduler de saldos) ======================
    bottom = ttk.Frame(root)
    bottom.pack(side="bottom", fill="x", padx=15, pady=(5, 15))

    scheduler_status_var = tk.StringVar()
    scheduler = ctx.register_process(ManagedProcess(
        root=root,
        launch_cmd=lambda: ctx.get_launch_cmd(['--scheduler']),
        running_text="🟢  En ejecución (esperando horarios)",
    )).bind(scheduler_status_var, on_change=lambda running: (
        btn_iniciar.config(state=tk.DISABLED if running else tk.NORMAL),
        btn_detener.config(state=tk.NORMAL if running else tk.DISABLED),
    ))

    def iniciar_scheduler():
        if scheduler.running:
            messagebox.showinfo("Scheduler", "El scheduler ya está en ejecución.")
            return
        if not Path(config.get("credentials_path", "")).exists():
            messagebox.showerror("Error", "Archivo de credenciales no encontrado.")
            return

        scheduler.start()
        messagebox.showinfo("Scheduler", "Scheduler iniciado en segundo plano.\nPuedes seguir usando la interfaz.")

    def detener_scheduler():
        scheduler.stop()
        messagebox.showinfo("Scheduler", "Scheduler detenido.")

    def ejecutar_ahora():
        if not Path(config.get("credentials_path", "")).exists():
            messagebox.showerror("Error", "Archivo de credenciales no encontrado.")
            return
        import subprocess
        subprocess.Popen(
            ctx.get_launch_cmd(['--balance']),
            creationflags=subprocess.CREATE_NEW_CONSOLE
        )

    def on_closing():
        for proceso in ctx.processes:
            proceso.stop()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_closing)

    control_frame = ttk.Frame(bottom)
    control_frame.pack(side="left")

    btn_iniciar = ttk.Button(control_frame, text="▶  Iniciar Scheduler", command=iniciar_scheduler, style="Accent.TButton")
    btn_iniciar.pack(side="left", padx=3)

    btn_detener = ttk.Button(control_frame, text="⏹  Detener Scheduler", command=detener_scheduler, state="disabled")
    btn_detener.pack(side="left", padx=3)

    scheduler_status_label = ttk.Label(
        control_frame, textvariable=scheduler_status_var,
        foreground=ctx.ACCENT, font=("Segoe UI", 10, "bold")
    )
    scheduler_status_label.pack(side="left", padx=15)

    ttk.Button(bottom, text="⚡  Ejecutar Ahora", command=ejecutar_ahora, style="Accent.TButton").pack(side="left", padx=5)
    ttk.Button(bottom, text="Salir", command=on_closing).pack(side="right", padx=5)

    ttk.Label(bottom, text="v2.1 ·by Andres", foreground="#6c7086", font=("Segoe UI", 8)).pack(side="right", padx=10)

    notebook.pack(fill="both", expand=True, padx=15, pady=(15, 5))

    root.mainloop()
