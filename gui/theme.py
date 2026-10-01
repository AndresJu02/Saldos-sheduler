"""
Tema oscuro de la aplicación: paleta de colores + estilos ttk, y el
detalle de Windows para que la barra de título también se vea oscura.
"""
import ctypes
from tkinter import ttk


def apply_dark_titlebar(root):
    """Aplica el modo oscuro a la barra de título de Windows (DWM)."""
    try:
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
        # DWMWA_USE_IMMERSIVE_DARK_MODE = 20 (Windows 10 20H1+, Windows 11)
        valor = ctypes.c_int(2)   # 2 = oscuro
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(valor), ctypes.sizeof(valor))
    except Exception:
        pass


def apply_dark_theme(root) -> dict:
    """Configura ttk.Style con el tema oscuro y devuelve los colores para
    que las pestañas los reutilicen (fondos, bordes, botones, etc.)."""
    style = ttk.Style()
    style.theme_use("clam")

    BG = "#1e1e2e"
    FG = "#cdd6f4"
    ACCENT = "#89b4fa"
    ACCENT_HOVER = "#74c7ec"
    DARKER = "#181825"
    ENTRY_BG = "#313244"
    BUTTON_BG = "#45475a"

    style.configure(".", background=BG, foreground=FG, font=("Segoe UI", 10))
    style.configure("TFrame", background=BG)
    style.configure("TLabel", background=BG, foreground=FG)
    style.configure("TLabelframe", background=BG, foreground=FG, borderwidth=1, relief="solid", bordercolor="#585b70")
    style.configure("TLabelframe.Label", background=BG, foreground=ACCENT, font=("Segoe UI", 11, "bold"))
    style.configure("TNotebook", background=BG, borderwidth=0)
    style.configure("TNotebook.Tab", background=DARKER, foreground=FG, padding=[18, 8], font=("Segoe UI", 10))
    style.map("TNotebook.Tab",
              background=[("selected", ACCENT), ("active", "#45475a")],
              foreground=[("selected", "#1e1e2e")],
              padding=[("selected", [22, 10])])  # Pestaña más grande al seleccionar

    style.configure("Accent.TButton", background=ACCENT, foreground="#1e1e2e", borderwidth=0, padding=[15, 6], font=("Segoe UI", 10, "bold"))
    style.map("Accent.TButton", background=[("active", ACCENT_HOVER), ("disabled", "#585b70")])

    style.configure("TButton", background=BUTTON_BG, foreground=FG, borderwidth=0, padding=[10, 5])
    style.map("TButton", background=[("active", "#585b70")])

    # Sin esto, "clam" (el tema base) deja el fondo del Checkbutton en su
    # blanco por defecto -se nota sobre todo al pasar el mouse (estado
    # "active")-, porque nunca se le asignó ningún color propio como al
    # resto de los widgets.
    style.configure("TCheckbutton", background=BG, foreground=FG, font=("Segoe UI", 10))
    style.map("TCheckbutton", background=[("active", BG)], foreground=[("active", FG)])

    VERDE = "#a6e3a1"
    ROJO = "#f38ba8"
    # Variante para checkboxes de "Modo de prueba" (dry-run): verde cuando
    # está activado (modo prueba = no toca nada real) y rojo cuando está
    # apagado (modo real = ya escribe/actúa de verdad) -para que el estado
    # se note de un vistazo, no solo por la marca del check-.
    style.configure("Prueba.TCheckbutton", background=BG, font=("Segoe UI", 10, "bold"))
    style.map("Prueba.TCheckbutton",
              background=[("active", BG)],
              foreground=[("selected", VERDE), ("!selected", ROJO)])
    # El indicador nativo de "clam" (el cuadradito) no dibuja un tilde
    # claro sobre un fondo de color -se nota poco si está o no marcado-,
    # así que para este estilo se saca del layout: el propio texto hace de
    # check (ver gui/dialogs.py -> checkbox_modo_prueba, que antepone
    # "☑"/"☐" según el estado).
    style.layout("Prueba.TCheckbutton", [
        ("Checkbutton.padding", {"sticky": "nswe", "children": [
            ("Checkbutton.focus", {"side": "left", "sticky": "w", "children": [
                ("Checkbutton.label", {"sticky": "nswe"})
            ]})
        ]})
    ])

    style.configure("Treeview", background=ENTRY_BG, fieldbackground=ENTRY_BG, foreground=FG, rowheight=30, borderwidth=0)
    style.configure("Treeview.Heading", background=DARKER, foreground=ACCENT, font=("Segoe UI", 10, "bold"), borderwidth=0)
    style.map("Treeview", background=[("selected", ACCENT)], foreground=[("selected", "#1e1e2e")])

    style.configure("TEntry", fieldbackground=ENTRY_BG, foreground=FG, borderwidth=1, relief="solid", bordercolor="#585b70")

    # --- Combobox: evita el "flash" blanco al abrir/seleccionar una opción ---
    style.configure("TCombobox",
                     fieldbackground=ENTRY_BG, background=ENTRY_BG, foreground=FG,
                     arrowcolor=FG, bordercolor="#585b70", lightcolor=ENTRY_BG, darkcolor=ENTRY_BG,
                     selectbackground=ENTRY_BG, selectforeground=FG, insertcolor=FG)
    style.map("TCombobox",
              fieldbackground=[("readonly", ENTRY_BG), ("disabled", ENTRY_BG), ("!disabled", ENTRY_BG)],
              foreground=[("readonly", FG), ("disabled", "#6c7086")],
              background=[("readonly", ENTRY_BG), ("active", BUTTON_BG)],
              selectbackground=[("readonly", ENTRY_BG)],
              selectforeground=[("readonly", FG)])
    # El menú desplegable (popdown) de Combobox no es un widget ttk, así que se
    # colorea aparte con option_add para que combine con el tema oscuro.
    root.option_add("*TCombobox*Listbox.background", ENTRY_BG)
    root.option_add("*TCombobox*Listbox.foreground", FG)
    root.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
    root.option_add("*TCombobox*Listbox.selectForeground", "#1e1e2e")
    root.option_add("*TCombobox*Listbox.font", ("Segoe UI", 10))

    return {
        "BG": BG, "FG": FG, "ACCENT": ACCENT, "ACCENT_HOVER": ACCENT_HOVER,
        "DARKER": DARKER, "ENTRY_BG": ENTRY_BG, "BUTTON_BG": BUTTON_BG,
    }
