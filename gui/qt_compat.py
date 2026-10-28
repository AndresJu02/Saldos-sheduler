"""
Puente mínimo entre el código existente (pensado para la API de Tkinter)
y Qt, para no tener que reescribir TODO lo que depende de root.after() /
root.after_cancel() -que se usa para hacer polling y para despachar
resultados de hilos en segundo plano de vuelta al hilo de la UI, en
gui/process_control.py, gui/log_viewer.py, gui/app.py y varias pestañas-.

AfterCapableWindow agrega esos dos métodos con la MISMA firma que sus
equivalentes de Tkinter (root.after(ms, fn) -> "id" cancelable con
root.after_cancel(id)), respaldados por QTimer.

OJO con los hilos: a diferencia de Tkinter (donde llamar a root.after()
desde un hilo de fondo "funcionaba" en la práctica), Qt NO permite crear
un QTimer hijo de la ventana principal desde un hilo que no sea el de la
GUI -tira "QObject: Cannot create children for a parent that is in a
different thread" y después deja esa ventana rota (reportado 2026-10-28,
al instalar Tesseract/consultar la Tienda DID: ambos disparan root.after(0, ...)
desde el hilo de fondo que hace la descarga/consulta)-. Por eso, cuando
after() se llama desde otro hilo, en vez de crear el QTimer ahí mismo se
manda el pedido por una señal Qt (con QueuedConnection), que el propio Qt
entrega de forma segura en el hilo de la GUI -recién ahí se crea el
QTimer-.
"""
from PyQt6.QtCore import QObject, QTimer, QThread, Qt, pyqtSignal


class _AfterBridge(QObject):
    """QObject aparte (no el propio AfterCapableWindow) a propósito: este
    mixin se combina con QMainWindow vía herencia múltiple, y PyQt no
    admite dos ramas QObject distintas en la misma clase -por eso la señal
    vive en un objeto propio, no en el mixin ni en la ventana-."""
    requested = pyqtSignal(object, int)


class AfterCapableWindow:
    """Mixin: agregar ANTES de QMainWindow/QWidget en la herencia, y
    llamar a self._init_after_bridge() una vez construida la ventana
    (en el hilo de la GUI -ver gui/app.py-) antes de que cualquier hilo
    de fondo pueda llegar a usar after()."""

    def _init_after_bridge(self):
        self._after_bridge = _AfterBridge(self)
        self._after_bridge.requested.connect(self._crear_timer, Qt.ConnectionType.QueuedConnection)

    def _crear_timer(self, fn, ms):
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(fn)
        timer.start(max(ms, 0))

    def after(self, ms, fn):
        if QThread.currentThread() is self.thread():
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(fn)
            timer.start(max(ms, 0))
            return timer
        else:
            # Pedido desde un hilo de fondo: no se puede crear el QTimer acá
            # mismo (ver nota del módulo). Se entrega el pedido por señal y
            # listo -no hay "id" útil para cancelar este caso particular,
            # pero ningún caller de after_cancel() depende de un after(0, ...)
            # lanzado desde un hilo de fondo (son todos "fire and forget")-.
            self._after_bridge.requested.emit(fn, ms)
            return None

    def after_cancel(self, timer_id):
        if timer_id is not None:
            try:
                timer_id.stop()
            except RuntimeError:
                pass  # el QTimer (o su ventana dueña) ya fue destruido


class LabelVar:
    """Mismo espíritu que AfterCapableWindow, pero para
    gui/process_control.py: ManagedProcess.bind(status_var) solo necesita
    un objeto con .set(texto) (y de regalo .get()) para mostrar "En
    ejecución"/"Detenido" -antes era un tk.StringVar atado a un ttk.Label
    con textvariable=, ahora es este envoltorio de un QLabel normal."""

    def __init__(self, label):
        self._label = label

    def set(self, texto):
        self._label.setText(texto)

    def get(self):
        return self._label.text()
