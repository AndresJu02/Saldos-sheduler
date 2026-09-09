"""
Cómo relanzar la aplicación como subproceso (para --scheduler, --balance,
--cargas-voip, --auto-like-tickets), tanto en modo desarrollo como
empaquetada con PyInstaller.
"""
import sys
from typing import List

from .paths import MAIN_SCRIPT_PATH


def get_launch_cmd(args: List[str]) -> List[str]:
    if getattr(sys, 'frozen', False):
        return [sys.executable] + args
    else:
        return [sys.executable, MAIN_SCRIPT_PATH] + args
