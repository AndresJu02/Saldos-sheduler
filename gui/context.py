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

    def register_process(self, managed_process: ManagedProcess) -> ManagedProcess:
        self.processes.append(managed_process)
        return managed_process
