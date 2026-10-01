"""Secciones plegables: mismo look que un ttk.LabelFrame (borde + título
en color de acento), pero con un encabezado cliqueable que despliega/
contrae el contenido -para no tener que scrollear entre secciones que no
se están usando en el momento-.

Uso (reemplaza la construcción de un LabelFrame normal):

    conexion_wrapper, conexion_frame = crear_seccion_plegable(cv_content, "🔌  Conexión Bitrix24")
    conexion_wrapper.pack(fill="x", padx=10, pady=(15, 10))

De ahí en más, `conexion_frame` se usa exactamente igual que el LabelFrame
de antes (.grid(), .columnconfigure(), etc. con los mismos hijos) -es un
ttk.Frame con el mismo padding=15-. Lo único que cambia es que ahora hay
que empacar `conexion_wrapper` (no `conexion_frame`) en el padre.

Las secciones "▶  Monitor" quedan A PROPÓSITO fuera de esto -el pedido
explícito fue que esas NO se puedan plegar-: siguen siendo un
ttk.LabelFrame común y corriente en cada pestaña.
"""
import tkinter as tk
from tkinter import ttk

BG = "#1e1e2e"
ACCENT = "#89b4fa"
BORDE = "#585b70"
HOVER = "#313244"


def crear_seccion_plegable(parent, titulo, abierta_por_defecto=True):
    estado = {"abierta": abierta_por_defecto}

    wrapper = ttk.Frame(parent)

    header = tk.Frame(wrapper, bg=BG, cursor="hand2")
    header.pack(fill="x")

    flecha_var = tk.StringVar()
    lbl_flecha = tk.Label(header, textvariable=flecha_var, bg=BG, fg=ACCENT,
                           font=("Segoe UI", 10, "bold"), cursor="hand2")
    lbl_flecha.pack(side="left", padx=(4, 6), pady=(3, 5))
    lbl_titulo = tk.Label(header, text=titulo, bg=BG, fg=ACCENT,
                           font=("Segoe UI", 11, "bold"), cursor="hand2")
    lbl_titulo.pack(side="left", pady=(3, 5))

    caja = tk.Frame(wrapper, bg=BG, highlightthickness=1, highlightbackground=BORDE)
    contenido = ttk.Frame(caja, padding=15)
    contenido.pack(fill="both", expand=True)

    widgets_clicables = (header, lbl_flecha, lbl_titulo)

    def _refrescar():
        flecha_var.set("▾" if estado["abierta"] else "▸")
        if estado["abierta"]:
            caja.pack(fill="both", expand=True)
        else:
            caja.pack_forget()

    def _alternar(_evt=None):
        estado["abierta"] = not estado["abierta"]
        _refrescar()

    def _hover_on(_evt=None):
        for w in widgets_clicables:
            w.configure(bg=HOVER)

    def _hover_off(_evt=None):
        for w in widgets_clicables:
            w.configure(bg=BG)

    for w in widgets_clicables:
        w.bind("<Button-1>", _alternar)
        w.bind("<Enter>", _hover_on)
        w.bind("<Leave>", _hover_off)

    _refrescar()
    return wrapper, contenido
