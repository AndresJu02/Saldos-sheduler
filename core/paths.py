"""
Rutas base de la aplicación (portable, funciona igual en modo desarrollo
que empaquetada con PyInstaller).

Todo lo demás en core/ y gui/ importa BASE_DIR de aquí en vez de
recalcularlo, para que exista un único lugar que sepa "dónde estamos".
"""
import sys
from pathlib import Path

if getattr(sys, 'frozen', False):
    BASE_DIR = Path(sys.executable).resolve().parent
    INTERNAL_DIR = Path(sys._MEIPASS)
else:
    # Este archivo vive en core/, la raíz del proyecto (donde está main.py)
    # es un nivel arriba.
    BASE_DIR = Path(__file__).resolve().parent.parent
    INTERNAL_DIR = BASE_DIR

# Ruta absoluta a main.py, usada para relanzar la aplicación como
# subproceso en modo desarrollo (`python main.py --scheduler`, etc.).
# En modo empaquetado no se usa (el propio .exe ya es el "intérprete").
MAIN_SCRIPT_PATH = str(BASE_DIR / "main.py")

LOG_FILE = BASE_DIR / 'scheduler.log'
CONFIG_FILE = BASE_DIR / 'scheduler_config.json'
CREDENTIALS_FILE = BASE_DIR / 'credenciales.json'
