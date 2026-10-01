"""Diálogos de aviso/confirmación con la misma estética oscura del resto
de la app, en vez del messagebox nativo de Tkinter (una ventana gris al
estilo Windows 95 que no combina con el tema oscuro ni con el resto de
los widgets, ya de por sí estilizados a mano en gui/theme.py).

Se usa exactamente igual que tkinter.messagebox -mismo nombre y firma
básica (título, mensaje) para showinfo/showerror/showwarning/askyesno-
así que en cada pestaña basta con cambiar el import:

    from tkinter import messagebox
      ->
    from gui import dialogs as messagebox

No hace falta ningún truco de transparencia/canvas para las esquinas
redondeadas: desde Windows 11 el propio compositor (DWM) ya redondea
las esquinas de cualquier ventana normal automáticamente -es justo lo
que ya se aprovecha en la ventana principal-, así que un Toplevel común
con la misma barra de título oscura (apply_dark_titlebar) ya se ve
nativo y moderno sin nada extra.
"""
import tkinter as tk
from tkinter import ttk

from gui.theme import apply_dark_titlebar

# Misma paleta que gui/theme.py (apply_dark_theme). Se duplica aquí en
# vez de importarla porque theme.py no expone esos valores como
# constantes de módulo, solo como resultado de apply_dark_theme().
BG = "#1e1e2e"
FG = "#cdd6f4"
ACCENT = "#89b4fa"
ENTRY_BG = "#313244"
MUTED = "#9399b2"
VERDE = "#a6e3a1"
AMARILLO = "#f9e2af"
ROJO = "#f38ba8"

_ESTILO_POR_TIPO = {
    "info": ("ℹ", ACCENT),
    "warning": ("⚠", AMARILLO),
    "error": ("✕", ROJO),
    "question": ("?", ACCENT),
}


def _parent():
    return tk._default_root


def _centrar(top, parent):
    top.update_idletasks()
    w, h = top.winfo_width(), top.winfo_height()
    if parent is not None and parent.winfo_ismapped():
        px, py = parent.winfo_rootx(), parent.winfo_rooty()
        pw, ph = parent.winfo_width(), parent.winfo_height()
        x, y = px + (pw - w) // 2, py + (ph - h) // 2
    else:
        sw, sh = top.winfo_screenwidth(), top.winfo_screenheight()
        x, y = (sw - w) // 2, (sh - h) // 2
    top.geometry(f"+{max(x, 0)}+{max(y, 0)}")


def _dialog(tipo, title, message, botones):
    """Arma y muestra el diálogo modal. `botones` es una lista de
    (texto, valor, es_accent), en el orden en que deben verse de
    izquierda a derecha (el último es el botón principal / default).
    Devuelve el valor del botón pulsado, o el del primero (el de
    "cancelar") si se cierra con la X o Escape."""
    parent = _parent()
    top = tk.Toplevel(parent)
    top.withdraw()
    top.title(title)
    top.configure(bg=BG)
    top.resizable(False, False)
    if parent is not None:
        top.transient(parent)
    apply_dark_titlebar(top)

    glifo, color = _ESTILO_POR_TIPO.get(tipo, _ESTILO_POR_TIPO["info"])
    resultado = {"valor": botones[0][1]}

    tk.Frame(top, height=3, bg=color).pack(fill="x", side="top")

    body = ttk.Frame(top, padding=(24, 22, 24, 18))
    body.pack(fill="both", expand=True)

    fila = ttk.Frame(body)
    fila.pack(fill="x")

    icono = tk.Canvas(fila, width=46, height=46, bg=BG, highlightthickness=0)
    icono.pack(side="left", padx=(0, 16), anchor="n")
    icono.create_oval(2, 2, 44, 44, fill=ENTRY_BG, outline=color, width=2)
    icono.create_text(23, 23, text=glifo, fill=color, font=("Segoe UI", 17, "bold"))

    texto = ttk.Frame(fila)
    texto.pack(side="left", fill="both", expand=True)
    ttk.Label(texto, text=title, font=("Segoe UI", 12, "bold"),
              foreground=FG, wraplength=320, justify="left").pack(anchor="w")
    ttk.Label(texto, text=message, font=("Segoe UI", 10),
              foreground=MUTED, wraplength=320, justify="left").pack(anchor="w", pady=(6, 0))

    fila_botones = ttk.Frame(body)
    fila_botones.pack(fill="x", pady=(20, 0))

    # Este diálogo es modal (grab_set) y "transient" de la ventana
    # principal -así Windows lo minimiza/restaura junto con ella-, pero esa
    # sincronización automática a veces falla: si se minimiza la app
    # mientras el diálogo está abierto, al restaurar puede quedar el
    # diálogo "perdido" invisible con el grab todavía activo, bloqueando
    # toda la app sin que se vea nada en pantalla (bug reportado 2026-09-28).
    # Se sincroniza a mano en vez de confiar en el comportamiento por
    # defecto: al minimizar la ventana principal se oculta el diálogo
    # también, y al restaurarla se lo vuelve a mostrar y enfocar.
    ids_binding = []
    if parent is not None:
        def _ocultar_con_padre(_event=None):
            if top.winfo_exists():
                top.withdraw()

        def _reaparecer_con_padre(_event=None):
            if top.winfo_exists():
                top.deiconify()
                top.lift()
                top.focus_force()

        ids_binding.append(("<Unmap>", parent.bind("<Unmap>", _ocultar_con_padre, add="+")))
        ids_binding.append(("<Map>", parent.bind("<Map>", _reaparecer_con_padre, add="+")))

    def cerrar_con(valor):
        if resultado.get("cerrado"):
            return
        resultado["cerrado"] = True
        resultado["valor"] = valor
        if parent is not None:
            for secuencia, bind_id in ids_binding:
                try:
                    parent.unbind(secuencia, bind_id)
                except Exception:
                    pass
        top.destroy()

    widgets_botones = []
    for texto_btn, valor, es_accent in reversed(botones):
        b = ttk.Button(fila_botones, text=texto_btn, style="Accent.TButton" if es_accent else "TButton",
                        command=lambda v=valor: cerrar_con(v))
        b.pack(side="right", padx=(8, 0))
        widgets_botones.append(b)

    principal = widgets_botones[0]  # el último de `botones` = primero en pack(side="right") invertido
    secundario_valor = botones[0][1]

    top.bind("<Return>", lambda e: cerrar_con(botones[-1][1]))
    top.bind("<Escape>", lambda e: cerrar_con(secundario_valor))
    top.protocol("WM_DELETE_WINDOW", lambda: cerrar_con(secundario_valor))

    _centrar(top, parent)
    top.deiconify()
    principal.focus_set()
    top.grab_set()
    top.wait_window(top)
    return resultado["valor"]


def showinfo(title="", message="", **kwargs):
    return _dialog("info", title, message, [("Aceptar", "ok", True)])


def showwarning(title="", message="", **kwargs):
    return _dialog("warning", title, message, [("Aceptar", "ok", True)])


def showerror(title="", message="", **kwargs):
    return _dialog("error", title, message, [("Aceptar", "ok", True)])


def askyesno(title="", message="", **kwargs):
    return _dialog("question", title, message, [("No", False, False), ("Sí", True, True)])


def checkbox_modo_prueba(parent, variable):
    """Checkbutton de "Modo de prueba" (dry-run) con estilo propio
    (Prueba.TCheckbutton, definido en gui/theme.py): verde con "☑" cuando
    está activado, rojo con "☐" cuando no. El indicador nativo de ttk se
    sacó del layout de ese estilo porque no se nota bien sobre un fondo de
    color -por eso el propio texto hace de check, actualizándose solo
    cada vez que cambia la variable-."""
    def _texto():
        return "☑  Modo de prueba" if variable.get() else "☐  Modo de prueba"

    chk = ttk.Checkbutton(parent, text=_texto(), variable=variable, style="Prueba.TCheckbutton")
    variable.trace_add("write", lambda *_: chk.configure(text=_texto()))
    return chk


def boton_ayuda(parent, texto, titulo="Información"):
    """Botón "❓" compacto para notas/tips que no hace falta tener siempre
    visibles en pantalla: al hacer clic, muestra `texto` en un diálogo
    (mismo estilo que showinfo). Para reemplazar los ttk.Label de aviso
    -foreground gris, letra chica- que antes quedaban permanentemente
    ocupando espacio en la pestaña."""
    return ttk.Button(parent, text="❓", width=3, command=lambda: showinfo(titulo, texto))
