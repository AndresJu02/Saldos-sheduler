"""
Contexto compartido entre la ventana principal (gui/app.py) y cada
pestaña (gui/tabs/*.py).

Cada módulo de pestaña recibe una instancia de AppContext con lo que
necesita para construirse: la ventana raíz, los colores del tema, la
configuración ya cargada (y cómo guardarla), cómo relanzar la app como
subproceso, y la lista de procesos en segundo plano que la ventana debe
detener al cerrarse.

¿Tu pestaña necesita algo que no está aquí? Este es el único lugar que
hay que tocar para "exponer" algo nuevo a todas las pestañas.
"""
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List

from gui.process_control import ManagedProcess
from gui.collapsible import crear_seccion_plegable


@dataclass
class AppContext:
    root: "tkinter.Tk"
    base_dir: Path
    config: dict
    save_config: Callable[[dict], None]
    get_launch_cmd: Callable[[list], list]

    # Colores del tema oscuro (ver gui/theme.py)
    BG: str
    FG: str
    ACCENT: str
    ACCENT_HOVER: str
    DARKER: str
    ENTRY_BG: str
    BUTTON_BG: str

    # Procesos en segundo plano registrados por las pestañas (Scheduler,
    # Cargas VOIP, Tickets, ...). gui/app.py los detiene todos al cerrar
    # la ventana (on_closing).
    processes: List[ManagedProcess] = field(default_factory=list)

    # Funciones que una pestaña registra para arrancar su monitor solo al
    # abrir la aplicación (por ahora: Tickets y Notificaciones). gui/app.py
    # las llama todas una vez confirma que la app no está bloqueada -ver
    # verificar_y_avisar()-.
    autostart_callbacks: List[Callable[[], None]] = field(default_factory=list)

    def register_process(self, managed_process: ManagedProcess) -> ManagedProcess:
        self.processes.append(managed_process)
        return managed_process

    def register_autostart(self, fn: Callable[[], None]) -> None:
        self.autostart_callbacks.append(fn)

    def seccion_plegable(self, parent, titulo: str, key: str, abierta_por_defecto: bool = True):
        """Como crear_seccion_plegable(), pero recuerda si el usuario la dejó
        plegada o desplegada entre una sesión y otra (persistido en
        scheduler_config.json bajo "ui_collapsed_sections"). `key` debe ser
        único en toda la app, ej. "cargas_voip.validacion"."""
        estados = self.config.setdefault("ui_collapsed_sections", {})
        abierta = estados.get(key, abierta_por_defecto)

        def _on_toggle(abierta_nueva):
            estados[key] = abierta_nueva
            self.save_config(self.config)

        return crear_seccion_plegable(parent, titulo, abierta_por_defecto=abierta, on_toggle=_on_toggle)
