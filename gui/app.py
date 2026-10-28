"""
Ventana principal: tema, pestañas (SegmentedWidget + QStackedWidget) y
barra inferior (control del Scheduler de saldos). Cada pestaña vive en su
propio archivo bajo gui/tabs/ — este módulo solo las ensambla.

Para agregar una pestaña nueva:
  1. Crear gui/tabs/mi_modulo.py con una función build(ctx) -> QWidget
     que arme y devuelva la página completa (ver cualquier pestaña
     existente para el patrón completo: tarjetas plegables con
     ctx.seccion_plegable(), sección "Monitor" fija, etc.).
  2. Importarla aquí y agregar una línea `_add_tab("Mi pestaña", mi_modulo.build, ctx)`
     junto a las demás, en el orden en que quieras que aparezca.
  3. Configuración siempre debe agregarse de última (queda a la derecha).
"""
import sys

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QStackedWidget, QFrame
from qfluentwidgets import SegmentedWidget, PrimaryPushButton, PushButton, BodyLabel, FluentIcon

from gui import dialogs as messagebox
from gui.qt_compat import AfterCapableWindow, LabelVar
from gui.widgets import pintar_boton_peligro

from remote_lock import verificar_bloqueo
from core.config import load_config, save_config
from core.version import VERSION
from core.launch import get_launch_cmd
from core.paths import BASE_DIR

from gui.theme import apply_dark_theme, apply_dark_titlebar
from gui.context import AppContext
from gui.process_control import ManagedProcess
from gui.tabs import proveedores, cargas_voip_tab, tickets_tab, didww_renovacion_tab, tienda_did_tab, notificaciones_tab, configuracion


class MainWindow(AfterCapableWindow, QMainWindow):
    """QMainWindow + .after()/.after_cancel() (ver gui/qt_compat.py), para
    que ManagedProcess, el visor de log y el polling de cada pestaña
    sigan funcionando igual que con el root.after() de Tkinter."""
    pass


def run_gui():
    app = QApplication(sys.argv)
    colors = apply_dark_theme()

    root = MainWindow()
    root._init_after_bridge()  # ver gui/qt_compat.py: hace falta ANTES de que cualquier hilo de fondo use root.after()
    root.setWindowTitle("LEYLA")
    try:
        root.setWindowIcon(QIcon(str(BASE_DIR / "icono.ico")))
    except Exception:
        pass
    # Ancho pensado para que quepan las 7 pestañas sin que se amontonen.
    root.resize(1000, 650)
    root.setMinimumSize(980, 560)

    central = QWidget()
    central.setStyleSheet(f"background-color: {colors['BG']};")
    root.setCentralWidget(central)
    main_layout = QVBoxLayout(central)
    main_layout.setContentsMargins(0, 0, 0, 0)
    main_layout.setSpacing(0)

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
            # Se cierre el diálogo con "Aceptar" o con la X: en cualquier
            # caso la app queda bloqueada, así que se cierra por completo
            # en vez de dejar la ventana abierta.
            on_closing()
            sys.exit(0)
        else:
            # Monitores que arrancan solos al abrir la app (por ahora:
            # Tickets y Notificaciones) -ver ctx.register_autostart() en
            # cada pestaña-. Se hace acá, después de confirmar que no está
            # bloqueada, para no competir con el aviso de arriba.
            for autostart in ctx.autostart_callbacks:
                try:
                    autostart()
                except Exception:
                    pass
    root.after(150, verificar_y_avisar)

    # ====================== PESTAÑAS ======================
    # SegmentedWidget (QFluentWidgets) ya dibuja la barra de pestañas como
    # un "segmented control" tipo macOS, con esquinas redondeadas de
    # verdad -lo que en la versión ttk/customtkinter no se podía lograr
    # del todo-. Cada módulo de pestaña expone build(ctx) -> QWidget (la
    # página completa); acá solo se agregan al stack y al selector.
    #
    # Dos SegmentedWidget separados (no uno solo): "pivot" para las
    # pestañas de trabajo y "pivot_utilidad" para 🔔/⚙, empujado del todo a
    # la derecha con un stretch en el medio -un solo SegmentedWidget no
    # permite meterle un hueco en el medio a sus propios ítems-.
    pivot = SegmentedWidget()
    pivot_utilidad = SegmentedWidget()
    stack = QStackedWidget()
    activadores = {}  # nombre -> _activar, para poder forzar la pestaña inicial más abajo

    def _add_tab(pivot_obj, nombre, build_fn, icon=None, texto=None):
        page = build_fn(ctx)
        page.setObjectName(nombre)
        stack.addWidget(page)

        # OJO: SegmentedWidget conecta onClick a una señal Qt que manda un
        # bool (itemClicked = pyqtSignal(bool), ver qfluentwidgets). Si esta
        # función tuviera "page" como parámetro (aunque sea con default,
        # ej. "def _activar(page=page)"), Qt la toma como el destino de ese
        # bool y lo pisa -"page" terminaba valiendo True/False en vez del
        # QWidget, reportado 2026-10-28 (TypeError: argument 1 has
        # unexpected type 'bool')-. "page" se sigue capturando por closure
        # sin problema (cada llamada a _add_tab tiene su propio "page"); acá
        # solo hace falta un parámetro que absorba ese bool y no se use.
        def _activar(_checked=False):
            stack.setCurrentWidget(page)
            pivot_obj.setCurrentItem(nombre)
            # Algunas pestañas (ej. Configuración, que refresca la API Key
            # de DIDWW por si se cambió desde Proveedores) necesitan saber
            # cuándo el usuario vuelve a verlas -ver gui/tabs/configuracion.py-.
            activar = getattr(page, "on_activated", None)
            if activar is not None:
                activar()

        pivot_obj.addItem(routeKey=nombre, text=nombre if texto is None else texto, icon=icon, onClick=_activar)
        activadores[nombre] = _activar

    _add_tab(pivot, "Proveedores", proveedores.build)
    _add_tab(pivot, "Cargas VOIP", cargas_voip_tab.build)
    _add_tab(pivot, "Tickets", tickets_tab.build)
    _add_tab(pivot, "Renovación DIDWW", didww_renovacion_tab.build)
    _add_tab(pivot, "Tienda DID", tienda_did_tab.build)
    _add_tab(pivot_utilidad, "🔔", notificaciones_tab.build)
    # Emoji de engranaje como texto (igual que "🔔" arriba) -no usar
    # icon=FluentIcon.SETTING: ese parámetro activa el padding asimétrico
    # "hasIcon=true" de PushButton (36px a la izquierda vs 12px a la
    # derecha), lo que deja el recuadro de selección descentrado respecto
    # al ícono y el botón más ancho de lo necesario (reportado 2026-10-28).
    _add_tab(pivot_utilidad, "Configuración", configuracion.build, texto="⚙")
    stack.setCurrentIndex(0)  # valor inicial sensato mientras carga; lo confirma _activar() de abajo

    tabbar_row = QHBoxLayout()
    tabbar_row.setContentsMargins(15, 5, 15, 5)
    tabbar_row.addWidget(pivot)
    tabbar_row.addStretch(1)
    tabbar_row.addWidget(pivot_utilidad)
    main_layout.addLayout(tabbar_row)
    main_layout.addWidget(stack, 1)

    # ====================== BARRA INFERIOR (Scheduler de saldos) ======================
    bottom = QFrame()
    bottom.setStyleSheet(f"background-color: {colors['DARKER']}; border-radius: 14px;")
    bottom_layout = QHBoxLayout(bottom)
    bottom_layout.setContentsMargins(15, 10, 15, 10)

    scheduler_status_label = BodyLabel()
    scheduler_status_label.setStyleSheet(f"color: {colors['ACCENT']}; font-weight: bold;")
    scheduler_status_var = LabelVar(scheduler_status_label)

    scheduler = ctx.register_process(ManagedProcess(
        root=root,
        launch_cmd=lambda: ctx.get_launch_cmd(['--scheduler']),
        running_text="🟢  En ejecución (esperando horarios)",
    )).bind(scheduler_status_var, on_change=lambda running: (
        btn_iniciar.setEnabled(not running),
        btn_detener.setEnabled(running),
    ))

    def iniciar_scheduler():
        if scheduler.running:
            messagebox.showinfo("Scheduler", "El scheduler ya está en ejecución.")
            return
        from pathlib import Path
        if not Path(config.get("credentials_path", "")).exists():
            messagebox.showerror("Error", "Archivo de credenciales no encontrado.")
            return

        scheduler.start()
        messagebox.showinfo("Scheduler", "Scheduler iniciado en segundo plano.\nPuedes seguir usando la interfaz.")

    def detener_scheduler():
        scheduler.stop()
        messagebox.showinfo("Scheduler", "Scheduler detenido.")

    def ejecutar_ahora():
        from pathlib import Path
        if not Path(config.get("credentials_path", "")).exists():
            messagebox.showerror("Error", "Archivo de credenciales no encontrado.")
            return
        if verificar_bloqueo():
            messagebox.showwarning(
                "Aplicación bloqueada",
                "⚠️  Esta aplicación ha sido bloqueada por el administrador.\n\n"
                "Contacte al soporte para más información."
            )
            return
        import subprocess
        subprocess.Popen(
            ctx.get_launch_cmd(['--balance']),
            creationflags=subprocess.CREATE_NEW_CONSOLE
        )

    def on_closing():
        for proceso in ctx.processes:
            proceso.stop()
        root.close()

    btn_iniciar = PrimaryPushButton(FluentIcon.PLAY, "Iniciar Scheduler")
    btn_iniciar.clicked.connect(iniciar_scheduler)
    bottom_layout.addWidget(btn_iniciar)

    btn_detener = PushButton(FluentIcon.PAUSE, "Detener Scheduler")
    btn_detener.setEnabled(False)
    btn_detener.clicked.connect(detener_scheduler)
    bottom_layout.addWidget(btn_detener)

    bottom_layout.addSpacing(10)
    bottom_layout.addWidget(scheduler_status_label)

    # "Ejecutar Ahora" y la versión van pegados al estado del scheduler (no
    # al otro extremo de la barra, separados por un hueco enorme) -el
    # stretch se corrió para después de ellos, así "Salir" sigue quedando
    # solo, pegado del todo a la derecha (reportado 2026-10-28, con cuadros
    # de colores marcando exactamente dónde debía quedar cada uno).
    bottom_layout.addSpacing(16)
    # QFluentWidgets no trae un ícono de rayo -se probaron todos los nombres
    # parecidos (POWER_BUTTON, SPEED_HIGH, etc), ninguno es un rayo de
    # verdad-, así que en vez de un ícono se usa el emoji "⚡" como parte
    # del texto del botón (2026-10-28).
    btn_ejecutar = PrimaryPushButton("⚡  Ejecutar Ahora")
    btn_ejecutar.clicked.connect(ejecutar_ahora)
    bottom_layout.addWidget(btn_ejecutar)

    bottom_layout.addSpacing(28)
    version_label = BodyLabel(f"v{VERSION} ·by Andres")
    version_label.setStyleSheet("color: #6c6c70;")
    bottom_layout.addWidget(version_label)

    bottom_layout.addStretch(1)

    btn_salir = PushButton("Salir")
    # pintar_boton_peligro (no un setStyleSheet("QPushButton{...}") suelto)
    # -ese selector genérico le ganaba al "PushButton" propio de
    # QFluentWidgets y le hacía perder el fondo/borde redondeado por
    # completo, dejando solo el texto rojo flotando sin botón alrededor
    # (reportado 2026-10-28, marcado con un recuadro verde).
    pintar_boton_peligro(btn_salir)
    btn_salir.clicked.connect(on_closing)
    bottom_layout.addWidget(btn_salir)

    main_layout.addWidget(bottom)

    root.show()
    apply_dark_titlebar(root)  # después de show(): recién ahí winId() es el HWND real y visible

    # Fuerza la pestaña inicial DESPUÉS de mostrar la ventana (y por el
    # mismo camino que un clic real -activadores[...]-, no solo
    # stack.setCurrentIndex): con la ventana recién armada, antes de
    # mostrarse, se vieron casos donde el SegmentedWidget terminaba
    # mostrando otra pestaña como seleccionada (reportado 2026-10-28,
    # inconsistente entre arranques) -probablemente por geometría/eventos
    # de layout todavía no resueltos en ese momento-. Haciéndolo acá, ya
    # con la ventana visible, queda determinístico.
    root.after(0, lambda: activadores["Proveedores"]())

    sys.exit(app.exec())
