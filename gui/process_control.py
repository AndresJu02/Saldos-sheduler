"""
Manejo de un proceso en segundo plano lanzado por la GUI (Scheduler,
Cargas VOIP, Tickets, ...). Antes esta misma lógica de iniciar / detener /
consultar estado con polling estaba copiada casi igual en cada pestaña;
esto la centraliza en un solo lugar.

Uso típico dentro de una pestaña:

    proc = ctx.register_process(ManagedProcess(
        root=ctx.root,
        launch_cmd=lambda: ctx.get_launch_cmd(['--cargas-voip']),
    ))
    proc.bind(status_var)
    ...
    btn_iniciar["command"] = lambda: proc.start()
    btn_detener["command"] = lambda: proc.stop()
"""
import subprocess
from typing import Callable, List, Optional


class ManagedProcess:
    def __init__(
        self,
        root,
        launch_cmd: Callable[[], List[str]],
        running_text: str = "🟢  En ejecución",
        stopped_text: str = "⚫  Detenido",
        creationflags: int = subprocess.CREATE_NO_WINDOW,
    ):
        self.root = root
        self._launch_cmd = launch_cmd
        self.running_text = running_text
        self.stopped_text = stopped_text
        self.creationflags = creationflags
        self.process: Optional[subprocess.Popen] = None
        self.status_var = None
        self._on_change: Optional[Callable[[bool], None]] = None

    def bind(self, status_var, on_change: Optional[Callable[[bool], None]] = None) -> "ManagedProcess":
        """status_var: tk.StringVar a actualizar con el estado.
        on_change(running: bool): callback opcional, útil para
        habilitar/deshabilitar los botones de Iniciar/Detener."""
        self.status_var = status_var
        self.status_var.set(self.stopped_text)
        self._on_change = on_change
        return self

    @property
    def running(self) -> bool:
        return self.process is not None

    def start(self) -> bool:
        if self.process is not None:
            return False
        self.process = subprocess.Popen(self._launch_cmd(), creationflags=self.creationflags)
        self._set_running(True)
        self.root.after(2000, self._poll)
        return True

    def _poll(self):
        if self.process is None:
            return
        if self.process.poll() is None:
            self.root.after(2000, self._poll)
        else:
            self.process = None
            self._set_running(False)

    def stop(self, timeout: int = 5):
        if self.process is None:
            return
        try:
            self.process.terminate()
            self.process.wait(timeout=timeout)
        except Exception:
            try:
                self.process.kill()
            except Exception:
                pass
        self.process = None
        self._set_running(False)

    def _set_running(self, running: bool):
        if self.status_var:
            self.status_var.set(self.running_text if running else self.stopped_text)
        if self._on_change:
            self._on_change(running)
