"""Rotación diaria de logs para monitores que se prenden y apagan de forma
intermitente (no quedan corriendo 24/7).

logging.handlers.TimedRotatingFileHandler decide CUÁNDO le toca rotar a
partir de la fecha de modificación que YA TENÍA el archivo cuando se creó
el handler -no de "ahora"- (ver CPython, TimedRotatingFileHandler.__init__:
usa os.stat(filename).st_mtime si el archivo existe). Eso funciona bien
para un proceso que corre sin parar, pero Cargas VOIP, Renovación DIDWW y
Tickets se prenden y apagan varias veces al día y quedan apagados días
enteros (el equipo los rota entre 3 PC): si el archivo no se vuelve a
escribir hasta bastante después de cruzar una medianoche, el próximo
arranque puede no disparar ningún rollover -y la limpieza de backups
viejos (backupCount) SOLO ocurre como efecto secundario de un rollover
real-. Resultado observado en producción: backups de hace más de una
semana que nunca se limpian, y el log "de hoy" que en realidad arrastra
varios días de mensajes sin cortar.

configurar_logger_rotativo() evita depender de esa lógica interna: antes
de crear el handler, si el archivo de log YA EXISTE y su fecha de
modificación es de un día anterior a hoy, lo rota A MANO (con el sufijo de
esa fecha) y limpia los backups que sobren de backup_count -así cada
arranque garantiza que el log del día empiece vacío, sin importar cuántos
días llevaba apagado el monitor-. El TimedRotatingFileHandler se sigue
usando después para el caso en que el monitor SÍ quede corriendo sin parar
durante la medianoche -ahí su rotación automática funciona bien, porque la
fecha de partida ya no está vencida-.
"""
import glob
import logging
from datetime import date, datetime
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path


def _rotar_si_es_de_otro_dia(log_file: Path, backup_count: int):
    if not log_file.exists():
        return
    fecha_archivo = datetime.fromtimestamp(log_file.stat().st_mtime).date()
    if fecha_archivo >= date.today():
        return  # ya se escribió hoy (o el reloj del sistema anda raro) -no hace falta rotar-

    destino = log_file.with_name(f"{log_file.name}.{fecha_archivo.isoformat()}")
    try:
        if destino.exists():
            # Ya existe un backup con esa fecha (ej. el monitor se reinició
            # varias veces ese mismo día viejo sin llegar a rotar): se le
            # agrega lo que falte en vez de perderlo.
            with open(destino, "a", encoding="utf-8") as dst, \
                 open(log_file, "r", encoding="utf-8", errors="replace") as src:
                dst.write(src.read())
            log_file.unlink()
        else:
            log_file.rename(destino)
    except OSError:
        return  # si por lo que sea no se puede rotar ahora, se sigue escribiendo en el mismo archivo

    _limpiar_backups_viejos(log_file, backup_count)


def _limpiar_backups_viejos(log_file: Path, backup_count: int):
    if backup_count <= 0:
        return
    backups = sorted(Path(p) for p in glob.glob(f"{glob.escape(str(log_file))}.*"))
    for viejo in backups[:-backup_count]:
        try:
            viejo.unlink()
        except OSError:
            pass


def configurar_logger_rotativo(logger: logging.Logger, log_file: Path, backup_count: int = 3,
                                nivel: int = logging.DEBUG):
    """Reemplazo directo del patrón `_setup_logging()` que tenían duplicado
    cargas_voip.py, didww_renovacion.py y auto_like_tickets.py: limpia los
    handlers previos, rota a mano el log si quedó de un día anterior (ver
    el docstring del módulo) y deja enganchado un TimedRotatingFileHandler
    para lo que siga corriendo de ahí en más."""
    logger.handlers.clear()
    _rotar_si_es_de_otro_dia(log_file, backup_count)

    handler = TimedRotatingFileHandler(log_file, when="midnight", backupCount=backup_count, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(nivel)
    logger.propagate = False
