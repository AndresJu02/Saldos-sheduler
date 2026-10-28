"""
Tema oscuro "estilo macOS" de la aplicación, ahora sobre PyQt6 +
QFluentWidgets (antes era ttk + customtkinter -ver el historial de git de
este archivo si hace falta compararlos-). QFluentWidgets ya trae sus
propios componentes con esquinas redondeadas, tarjetas, acrílico/mica y
modo oscuro resueltos; acá solo se fija el modo oscuro, el color de
acento, y se devuelve la paleta en bruto para los pocos lugares que
todavía necesitan un color exacto (textos de estado en verde/rojo, etc.)
en vez de un componente ya temeado.
"""
import ctypes

from qfluentwidgets import setTheme, Theme, setThemeColor


def apply_dark_titlebar(window):
    """Aplica el modo oscuro a la barra de título de Windows (DWM). Mismo
    truco que con Tkinter: QMainWindow también expone winId() (el HWND
    nativo de Windows), así que la llamada a DWM es idéntica."""
    try:
        hwnd = int(window.winId())
        valor = ctypes.c_int(2)   # 2 = oscuro (DWMWA_USE_IMMERSIVE_DARK_MODE)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(valor), ctypes.sizeof(valor))
    except Exception:
        pass


def apply_dark_theme() -> dict:
    """Activa el modo oscuro + color de acento de QFluentWidgets (afecta
    automáticamente a todos sus componentes: CardWidget, PushButton,
    SegmentedWidget, etc.) y devuelve la paleta en bruto para el resto de
    la app (fondos de página, textos de estado)."""
    BG = "#1c1c1e"         # fondo de ventana / contenido de cada pestaña
    FG = "#f2f2f4"
    ACCENT = "#0a84ff"     # azul de sistema de macOS (también el accent color de Fluent)
    ACCENT_HOVER = "#3aa0ff"
    DARKER = "#141416"     # franja de la barra de pestañas / barra inferior
    ENTRY_BG = "#2c2c2e"   # fondo de listas/entradas que no vienen de QFluentWidgets
    BUTTON_BG = "#3a3a3c"  # fondo de botones secundarios planos
    PANEL = "#28282a"      # fondo de las tarjetas -un poco más claro que BG-
    VERDE = "#32d74b"
    ROJO = "#ff453a"
    AMARILLO = "#ffd60a"

    setTheme(Theme.DARK)
    setThemeColor(ACCENT)

    return {
        "BG": BG, "FG": FG, "ACCENT": ACCENT, "ACCENT_HOVER": ACCENT_HOVER,
        "DARKER": DARKER, "ENTRY_BG": ENTRY_BG, "BUTTON_BG": BUTTON_BG, "PANEL": PANEL,
        "VERDE": VERDE, "ROJO": ROJO, "AMARILLO": AMARILLO,
    }
