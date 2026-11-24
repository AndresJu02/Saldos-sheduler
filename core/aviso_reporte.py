"""
Avisa si no se paso el reporte de saldos al chat despues de un horario.

El reporte se envia a MANO (boton "Enviar captura al chat" de la pestana
Proveedores, ver core/captura_saldos.py), asi que es facil olvidarlo. Esto
vigila los horarios configurados y, si pasada media hora no hay reporte en
el chat, saca una notificacion de Windows; y si se sigue sin pasar, la
repite cada 10 minutos.

Por que se comprueba contra BITRIX y no contra un archivo local: las 3 PC
corren el mismo programa. Si cada una mirara solo lo que envio ella, en
cuanto un companero pasara el reporte las otras dos seguirian insistiendo.
El chat es el unico sitio donde las tres ven lo mismo.
"""
import logging
import re
from datetime import datetime, timedelta

from .paths import BASE_DIR

logger = logging.getLogger("main")

# Icono general de la app, NO el rojo de DIDWW: ese es el distintivo de
# los avisos del vigilante de renovaciones (ver el docstring de
# didww_renovacion_v2/desktop_alert.py) y reutilizarlo aqui haria que dos
# avisos de cosas distintas se vieran iguales.
ICONO = BASE_DIR / "icono_notificacion.png"
APP_ID = "Saldos Scheduler"


def _avisar_escritorio(titulo, mensaje):
    """Notificacion nativa de Windows. Llega aunque nadie este mirando la
    app, que es justo el caso: el reporte se olvida estando en otra cosa."""
    try:
        from winotify import Notification
        Notification(
            app_id=APP_ID, title=titulo, msg=mensaje,
            icon=str(ICONO) if ICONO.exists() else "", duration="long",
        ).show()
    except Exception as e:
        logger.error("No se pudo mostrar el aviso de escritorio: %s", e)

# Minutos despues del horario antes del primer aviso.
RETRASO_PRIMER_AVISO_MIN = 30

# Cada cuanto se repite mientras siga sin pasarse.
REPETIR_CADA_MIN = 10

# Despues de esto se deja de insistir por ese horario. Sin tope, el ultimo
# horario del dia seguiria avisando toda la noche.
DEJAR_DE_INSISTIR_MIN = 120

# Nombre con el que captura_saldos.py sube la imagen (saldos_AAAAMMDD_HHMMSS.png).
RE_ARCHIVO_REPORTE = re.compile(r"^saldos_\d{8}_\d{6}\.png$", re.IGNORECASE)


def _a_local(iso):
    """Convierte la fecha ISO que devuelve Bitrix (viene con la zona del
    portal, que no tiene por que ser la de esta PC) a hora local sin zona,
    para poder compararla con datetime.now()."""
    fecha = datetime.fromisoformat(iso)
    if fecha.tzinfo is not None:
        fecha = fecha.astimezone().replace(tzinfo=None)
    return fecha


def hora_ultimo_reporte(config, limite=20):
    """Cuando se publico el ultimo reporte en el chat configurado, o None
    si no se encontro ninguno (o no se pudo preguntar).

    Se reconoce por el ADJUNTO, no por el texto: el nombre del archivo lo
    pone el propio programa, mientras que el texto del mensaje es
    configurable y cualquiera podria escribir "Saldos" a mano en el chat."""
    import requests

    webhook = (config.get("bitrix_webhook_url") or "").strip()
    chat_id = ((config.get("captura_bitrix") or {}).get("chat_id") or "").strip()
    if not webhook or not chat_id:
        return None
    if not webhook.endswith("/"):
        webhook += "/"

    respuesta = requests.post(
        webhook + "im.dialog.messages.get",
        json={"DIALOG_ID": chat_id, "LIMIT": limite}, timeout=30,
    ).json()
    resultado = respuesta.get("result") or {}

    # Los archivos vienen aparte de los mensajes, con su propia fecha.
    fechas = []
    for archivo in (resultado.get("files") or []):
        if RE_ARCHIVO_REPORTE.match((archivo.get("name") or "").strip()):
            try:
                fechas.append(_a_local(archivo.get("date")))
            except Exception:
                continue
    return max(fechas) if fechas else None


def _horario_vencido(config, ahora):
    """El horario de HOY mas reciente que ya paso hace al menos
    RETRASO_PRIMER_AVISO_MIN minutos, o None si todavia no toca avisar por
    ninguno. Usa los mismos horarios que dispara el scheduler."""
    dia = ahora.weekday()
    if dia <= 4:
        horarios = config.get("horarios_lun_vie") or []
    elif dia == 5:
        horarios = config.get("horarios_sabado") or []
    else:
        return None

    vencidos = []
    for hhmm in horarios:
        try:
            hora, minuto = [int(x) for x in str(hhmm).strip().split(":")]
        except (ValueError, AttributeError):
            continue
        momento = ahora.replace(hour=hora, minute=minuto, second=0, microsecond=0)
        retraso = (ahora - momento).total_seconds() / 60
        if RETRASO_PRIMER_AVISO_MIN <= retraso <= DEJAR_DE_INSISTIR_MIN:
            vencidos.append(momento)
    return max(vencidos) if vencidos else None


def revisar(config, estado, avisar=None, ahora=None):
    """Mira si falta pasar el reporte y avisa cuando toca.

    `estado` es un dict que el llamador conserva entre vueltas; aca se
    guarda de que horario se aviso y cuando fue el ultimo aviso.

    Devuelve el texto del aviso mostrado, o None si no habia nada que
    avisar. No lanza: un fallo de red no debe tumbar el scheduler."""
    ahora = ahora or datetime.now()
    try:
        horario = _horario_vencido(config, ahora)
        if horario is None:
            estado.pop("horario", None)
            estado.pop("ultimo_aviso", None)
            return None

        enviado = hora_ultimo_reporte(config)
        if enviado is not None and enviado >= horario:
            # Ya se paso (lo haya hecho esta PC o la de un companero).
            if estado.get("horario") == horario:
                logger.info("Aviso de reporte: ya se paso el de las %s.",
                            horario.strftime("%H:%M"))
            estado.pop("horario", None)
            estado.pop("ultimo_aviso", None)
            return None

        ultimo = estado.get("ultimo_aviso")
        if estado.get("horario") == horario and ultimo is not None:
            if (ahora - ultimo) < timedelta(minutes=REPETIR_CADA_MIN):
                return None     # ya se aviso hace poco

        minutos = int((ahora - horario).total_seconds() / 60)
        # Solo ASCII a proposito: este texto va tambien al log y a la
        # consola, que en Windows suelen ser cp1252 y revientan con
        # caracteres como la flecha larga.
        mensaje = ("No se ha pasado el reporte de saldos de las %s "
                   "(%d minutos). Envialo desde Proveedores > Enviar captura al chat."
                   % (horario.strftime("%H:%M"), minutos))

        (avisar or _avisar_escritorio)("Falta pasar los saldos", mensaje)

        estado["horario"] = horario
        estado["ultimo_aviso"] = ahora
        logger.info("Aviso de reporte mostrado: %s", mensaje)
        return mensaje
    except Exception as e:
        logger.warning("No se pudo revisar si falta pasar el reporte: %s", e)
        return None
