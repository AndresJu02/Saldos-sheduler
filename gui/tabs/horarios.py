"""Pestaña "Horarios": horarios de la ronda automática de consulta de saldos."""
from tkinter import ttk, messagebox


def build(notebook, ctx):
    config = ctx.config

    tab_hor = ttk.Frame(notebook)
    notebook.add(tab_hor, text="   Horarios   ")

    hor_frame = ttk.LabelFrame(tab_hor, text="⏰  Configuración de horarios", padding=20)
    hor_frame.pack(fill="both", expand=True, padx=10, pady=10)

    ttk.Label(hor_frame, text="Lunes a Viernes:", font=("Segoe UI", 10, "bold")).grid(row=0, column=0, sticky="w", pady=5)
    entry_lv = ttk.Entry(hor_frame, width=50)
    entry_lv.insert(0, ",".join(config["horarios_lun_vie"]))
    entry_lv.grid(row=0, column=1, padx=15, pady=5)

    ttk.Label(hor_frame, text="Sábados:", font=("Segoe UI", 10, "bold")).grid(row=1, column=0, sticky="w", pady=5)
    entry_sab = ttk.Entry(hor_frame, width=50)
    entry_sab.insert(0, ",".join(config["horarios_sabado"]))
    entry_sab.grid(row=1, column=1, padx=15, pady=5)

    ttk.Label(hor_frame, text="Tolerancia (min):", font=("Segoe UI", 10, "bold")).grid(row=2, column=0, sticky="w", pady=5)
    entry_tol = ttk.Entry(hor_frame, width=10)
    entry_tol.insert(0, str(config["tolerancia_min"]))
    entry_tol.grid(row=2, column=1, padx=15, pady=5, sticky="w")

    def guardar_horarios():
        config["horarios_lun_vie"] = [h.strip() for h in entry_lv.get().split(",") if h.strip()]
        config["horarios_sabado"] = [h.strip() for h in entry_sab.get().split(",") if h.strip()]
        try:
            config["tolerancia_min"] = float(entry_tol.get())
        except ValueError:
            messagebox.showerror("Error", "La tolerancia debe ser un número.")
            return
        ctx.save_config(config)
        messagebox.showinfo("Guardado", "Horarios actualizados.")

    ttk.Button(hor_frame, text="💾  Guardar Horarios", command=guardar_horarios, style="Accent.TButton").grid(
        row=3, column=1, pady=20, sticky="e"
    )
