"""Diálogos de aviso/confirmación con la misma estética oscura del resto
de la app (QFluentWidgets ya los dibuja con esquinas redondeadas y fondo
oscuro solo con setTheme(Theme.DARK), ver gui/theme.py).

Se usa igual que antes (con tkinter.messagebox / la versión vieja de este
mismo archivo): showinfo/showerror/showwarning/askyesno con (título,
mensaje).

También vive acá `boton_ayuda()` (botón "?" compacto que abre un
showinfo) y `checkbox_modo_prueba()` (checkbox verde/rojo según esté
activado o no, para "Modo de prueba (dry-run)" en Cargas VOIP y
Renovación DIDWW) -se usan en varias pestañas."""
import webbrowser

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication
from qfluentwidgets import MessageBox, MessageBoxBase, TransparentToolButton, FluentIcon, CheckBox, BodyLabel, HyperlinkButton, SubtitleLabel

VERDE = "#32d74b"
ROJO = "#ff453a"


def _parent():
    return QApplication.activeWindow()


def _dialog(glifo, title, message, mostrar_cancelar, texto_si="Aceptar", texto_no="Cancelar"):
    w = MessageBox(f"{glifo}  {title}", message, _parent())
    w.yesButton.setText(texto_si)
    if mostrar_cancelar:
        w.cancelButton.setText(texto_no)
    else:
        w.hideCancelButton()
    return bool(w.exec())


def showinfo(title="", message="", **kwargs):
    return _dialog("ℹ", title, message, mostrar_cancelar=False)


def showwarning(title="", message="", **kwargs):
    return _dialog("⚠", title, message, mostrar_cancelar=False)


def showerror(title="", message="", **kwargs):
    return _dialog("✕", title, message, mostrar_cancelar=False)


def askyesno(title="", message="", **kwargs):
    return _dialog("?", title, message, mostrar_cancelar=True, texto_si="Sí", texto_no="No")


def boton_ayuda(texto, titulo="Información"):
    """Botón "?" compacto para notas/tips que no hace falta tener siempre
    visibles en pantalla: al hacer clic, muestra `texto` en un showinfo.
    Agregalo a tu layout con addWidget() como a cualquier otro widget."""
    btn = TransparentToolButton(FluentIcon.HELP)
    btn.setFixedSize(26, 26)
    btn.setToolTip(titulo)
    btn.clicked.connect(lambda: showinfo(titulo, texto))
    return btn


def mostrar_ayuda_larga(titulo, texto, enlace=None, texto_enlace=None):
    """Ventana de ayuda con un texto largo (instrucciones paso a paso) y
    un único botón "Cerrar" -reemplaza a los tk.Toplevel con instrucciones
    de gui/tabs/configuracion.py (webhook de Bitrix24, credenciales.json)-.
    `enlace`/`texto_enlace` opcionales agregan un botón tipo link arriba
    del texto (ej. abrir Google Cloud Console)."""
    w = MessageBoxBase(_parent())
    w.cancelButton.hide()
    w.yesButton.setText("Cerrar")

    w.viewLayout.addWidget(SubtitleLabel(titulo))
    if enlace:
        link_btn = HyperlinkButton(enlace, texto_enlace or enlace)
        w.viewLayout.addWidget(link_btn)
    cuerpo = BodyLabel(texto)
    cuerpo.setWordWrap(True)
    cuerpo.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    w.viewLayout.addWidget(cuerpo)
    w.widget.setMinimumWidth(560)
    w.exec()


def checkbox_modo_prueba(checked_inicial=True):
    """Checkbox de "Modo de prueba" (dry-run): verde cuando está activado
    (modo prueba = no toca nada real) y rojo cuando está apagado (modo
    real = ya escribe/actúa de verdad) -para que el estado se note de un
    vistazo-. El caller lee el valor final con chk.isChecked()."""
    chk = CheckBox("Modo de prueba (dry-run)")
    chk.setChecked(checked_inicial)

    def _actualizar_color():
        color = VERDE if chk.isChecked() else ROJO
        chk.setStyleSheet(f"QCheckBox{{color: {color}; font-weight: bold;}}")

    chk.stateChanged.connect(lambda _=None: _actualizar_color())
    _actualizar_color()
    return chk
