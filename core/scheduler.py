"""
Planificador: lanza una ronda de consulta de saldos (--balance) en los
horarios configurados, con un lock por hoja de cálculo para evitar que
dos instalaciones disparen la misma ronda al mismo tiempo.
"""
import time
import uuid
import socket
import subprocess
import logging
from pathlib import Path
from datetime import datetime

from .config import load_config
from .launch import get_launch_cmd

logger = logging.getLogger("main")


def run_scheduler():
    config = load_config()
    sh = None
    try:
        if Path(config['credentials_path']).exists():
            from oauth2client.service_account import ServiceAccountCredentials
            import gspread
            scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
            creds = ServiceAccountCredentials.from_json_keyfile_name(str(config['credentials_path']), scope)
            client = gspread.authorize(creds)
            sh = client.open_by_url(config['google_sheet_url']).sheet1
    except Exception:
        pass

    owner_id = f"{socket.gethostname()}_{uuid.uuid4().hex[:8]}"
    executed_today = set()
    current_date = datetime.now().date()

    logger.info("Scheduler iniciado.")
    while True:
        ahora = datetime.now()
        if ahora.date() != current_date:
            executed_today.clear()
            current_date = ahora.date()

        weekday = ahora.weekday()
        if weekday in (0, 1, 2, 3, 4):
            horarios = config['horarios_lun_vie']
        elif weekday == 5:
            horarios = config['horarios_sabado']
        else:
            horarios = []

        for hhmm in horarios:
            try:
                h, m = map(int, hhmm.split(':'))
                target = ahora.replace(hour=h, minute=m, second=0, microsecond=0)
                if abs((ahora - target).total_seconds()) > config['tolerancia_min'] * 60:
                    continue
                if hhmm in executed_today:
                    continue

                if sh:
                    try:
                        lock_val = f"{owner_id}|{ahora.isoformat()}"
                        sh.update_acell('Z1', lock_val)
                        read_back = sh.acell('Z1').value
                        if not read_back or read_back.split('|')[0] != owner_id:
                            continue
                    except Exception:
                        pass

                logger.info(f"Lanzando tarea programada para {hhmm}")
                subprocess.Popen(
                    get_launch_cmd(['--balance']),
                    creationflags=subprocess.CREATE_NEW_CONSOLE
                )
                executed_today.add(hhmm)

                if sh:
                    time.sleep(5)
                    try:
                        sh.update_acell('Z1', '')
                    except Exception:
                        pass
            except Exception as e:
                logger.error(f"Error en horario {hhmm}: {e}")

        time.sleep(config['sleep_interval'])
