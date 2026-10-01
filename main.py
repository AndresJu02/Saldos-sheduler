#!/usr/bin/env python3
"""
Aplicación unificada de saldos.
- Sin argumentos       → interfaz gráfica.
- --scheduler          → inicia el planificador en segundo plano.
- --balance            → ejecuta una ronda de consulta de saldos (con consola propia).
- --cargas-voip        → monitor de validación de cargas (Bitrix24 + OCR).
- --auto-like-tickets  → auto-like de tickets asignados (Bitrix24).
- --didww-renovacion   → renovación automática de líneas DIDWW (Bitrix24).

Este archivo es solo el punto de entrada. La lógica de la aplicación
vive organizada así:

  core/   → lógica sin interfaz gráfica (config, Chrome/ChromeDriver,
            Tesseract, proveedores, ciclo de saldos, planificador).
            Empieza por core/paths.py si necesitas entender rutas base.
  gui/    → la interfaz gráfica. gui/app.py arma la ventana; cada
            pestaña vive en su propio archivo bajo gui/tabs/.
  cargas_voip.py, auto_like_tickets.py → monitores de Bitrix24
            (uno por chat/funcionalidad), cada uno con su propia
            configuración y log.
  providers/ → un archivo por proveedor de saldos (sobre BaseProvider).
"""
import sys
import ctypes
import logging

from core.paths import LOG_FILE

# ---------------------------------------------------------------------------
# Logging (solo archivo) — se configura una única vez aquí; el resto de
# módulos de core/ usan logging.getLogger("main") y comparten este handler.
# ---------------------------------------------------------------------------
logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)


# ---------------------------------------------------------------------------
# Consola dinámica para la ejecución de saldos
# ---------------------------------------------------------------------------
def setup_console():
    """Adjunta o crea una consola y redirige entrada/salida."""
    if not getattr(sys, 'frozen', False):
        return
    kernel32 = ctypes.windll.kernel32
    if kernel32.AttachConsole(-1) == 0:
        kernel32.AllocConsole()
    sys.stdout = open('CONOUT$', 'w')
    sys.stderr = open('CONOUT$', 'w')
    sys.stdin = open('CONIN$', 'r')


# ---------------------------------------------------------------------------
# Punto de entrada
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    if len(sys.argv) > 1:
        if sys.argv[1] == '--scheduler':
            from core.scheduler import run_scheduler
            run_scheduler()
        elif sys.argv[1] == '--balance':
            setup_console()
            from core.config import load_config
            from core.balance import run_balance_cycle
            run_balance_cycle(load_config())
        elif sys.argv[1] == '--cargas-voip':
            import cargas_voip
            cargas_voip.run()
        elif sys.argv[1] == '--auto-like-tickets':
            import auto_like_tickets
            auto_like_tickets.run()
        elif sys.argv[1] == '--didww-renovacion':
            import didww_renovacion
            didww_renovacion.run()
        else:
            print("Argumento desconocido. Use --scheduler, --balance, --cargas-voip, "
                  "--auto-like-tickets o --didww-renovacion.")
    else:
        from gui.app import run_gui
        run_gui()
