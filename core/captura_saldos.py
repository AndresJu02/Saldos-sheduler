"""
Captura la hoja de saldos y la publica como imagen en un chat de Bitrix.

Se ejecuta al final del chequeo de saldos (ver el cierre de ejecutar() en
core/balance.py). Pensado para que el equipo vea el resultado en el chat
sin tener que abrir la hoja.

Como se hace, y por que asi:

1. CAPTURA. Se abre la vista "/preview" de Google Sheets con el mismo
   Chrome portable que usan los proveedores. Se usa /preview y no la URL
   normal de edicion porque la de edicion trae toda la interfaz del editor
   (barras, menus, cinta) y porque /preview NO pide iniciar sesion: la
   hoja del equipo es de acceso publico -comprobado 2026-11-24-, asi que
   no hay que mantener ninguna sesion de Google en el perfil de Chrome.

   No se intenta recortar leyendo el DOM: /preview dibuja la rejilla en un
   canvas, no en <td>, asi que buscar celdas devuelve solo la pestana de
   abajo (probado 2026-11-24). El recorte se hace por pixeles.

2. ENVIO. Bitrix no acepta una imagen en im.message.add directamente. El
   camino bueno es el pensado para chats:
       im.disk.folder.get(CHAT_ID)  -> carpeta de archivos de ese chat
       disk.folder.uploadfile       -> sube el png ahi
       im.disk.file.commit          -> lo publica como mensaje del chat
   El webhook del equipo tiene los permisos 'disk' e 'im' que esto pide.

Nada de aqui puede tumbar el chequeo de saldos: enviar_resumen() atrapa
todo y devuelve (ok, detalle) en vez de propagar excepciones. Si falla el
envio, los saldos ya quedaron escritos en la hoja igual.
"""
import base64
import logging
import time
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("main")

# Margen en pixeles que se deja alrededor del contenido al recortar.
MARGEN_RECORTE = 12

# Alto aproximado de la barra de pestanas ("Sheet1") que /preview pinta
# abajo del todo. Se descarta antes de medir el contenido: si no, cuenta
# como contenido y el recorte no quita nada.
ALTO_BARRA_PESTANAS = 50

ESPERA_RENDER_SEG = 7


def _url_preview(url_hoja):
    """Convierte la URL de edicion configurada en la de solo lectura.

    Acepta tanto .../edit?gid=0 como .../preview, y conserva el gid para
    capturar la misma pestana que mira el equipo."""
    url = (url_hoja or "").strip()
    if not url:
        return ""
    gid = ""
    if "gid=" in url:
        gid = url.split("gid=")[1].split("&")[0].split("#")[0]
    base = url.split("/edit")[0].split("/preview")[0].split("/view")[0].rstrip("/")
    return base + "/preview" + (("?gid=" + gid) if gid else "")


def capturar_hoja(url_hoja, chrome_exe, chromedriver_exe, destino, caja_manual=None):
    """Guarda en `destino` un PNG de la hoja, recortado al contenido.
    Devuelve la ruta, o lanza excepcion si no se pudo."""
    from selenium.webdriver import Chrome, ChromeOptions
    from selenium.webdriver.chrome.service import Service

    url = _url_preview(url_hoja)
    if not url:
        raise RuntimeError("No hay URL de hoja configurada.")

    opciones = ChromeOptions()
    opciones.binary_location = chrome_exe
    for arg in ("--headless=new", "--window-size=1600,1000", "--no-sandbox",
                "--disable-gpu", "--hide-scrollbars", "--disable-dev-shm-usage"):
        opciones.add_argument(arg)

    driver = Chrome(service=Service(chromedriver_exe), options=opciones)
    try:
        driver.get(url)
        time.sleep(ESPERA_RENDER_SEG)   # /preview tarda en pintar la rejilla
        destino = Path(destino)
        driver.save_screenshot(str(destino))
    finally:
        try:
            driver.quit()
        except Exception:
            pass

    _recortar(destino, caja_manual)
    return destino


def _recortar(ruta, caja_manual=None):
    """Recorta el PNG al contenido real. Si algo falla, deja la imagen
    completa: una captura con mucho blanco sigue siendo util.

    caja_manual ("x1,y1,x2,y2") fuerza un encuadre fijo. Sirve porque el
    recorte automatico solo puede quitar los bordes vacios: si la hoja
    tiene algo suelto a la derecha (en esta hoja, el cuadro "HORA
    ACTUALIZACION"), el recto abarca hasta ahi y queda mucho blanco en
    medio. Con la caja manual se encuadra solo la tabla."""
    try:
        from PIL import Image, ImageChops
    except ImportError:
        return
    try:
        if caja_manual:
            numeros = [int(n) for n in str(caja_manual).replace(" ", "").split(",")]
            if len(numeros) == 4:
                Image.open(ruta).convert("RGB").crop(tuple(numeros)).save(ruta)
                return
        imagen = Image.open(ruta).convert("RGB")
        ancho, alto = imagen.size
        cuerpo = imagen.crop((0, 0, ancho, max(1, alto - ALTO_BARRA_PESTANAS)))
        caja = ImageChops.difference(
            cuerpo, Image.new("RGB", cuerpo.size, (255, 255, 255))
        ).getbbox()
        if not caja:
            return
        m = MARGEN_RECORTE
        cuerpo.crop((
            max(0, caja[0] - m), max(0, caja[1] - m),
            min(cuerpo.size[0], caja[2] + m), min(cuerpo.size[1], caja[3] + m),
        )).save(ruta)
    except Exception as e:
        logger.warning(f"No se pudo recortar la captura ({e}); se envia completa.")


def _api(webhook, metodo, **parametros):
    import requests
    url = webhook if webhook.endswith("/") else webhook + "/"
    respuesta = requests.post(url + metodo, json=parametros, timeout=120).json()
    if respuesta.get("error"):
        raise RuntimeError(f"{metodo}: {respuesta.get('error_description') or respuesta['error']}")
    return respuesta.get("result")


def enviar_a_bitrix(webhook, chat_id, ruta_png, mensaje=""):
    """Publica `ruta_png` como archivo dentro del chat indicado.

    chat_id acepta tanto "chat265" como 265: im.* quiere el numero."""
    numero = str(chat_id).strip().lower().replace("chat", "")
    if not numero.isdigit():
        raise RuntimeError(f"Id de chat no valido: {chat_id!r} (se espera algo como 'chat265').")
    numero = int(numero)

    carpeta = _api(webhook, "im.disk.folder.get", CHAT_ID=numero)
    if not carpeta or not carpeta.get("ID"):
        raise RuntimeError(f"El chat {chat_id} no tiene carpeta de archivos accesible.")

    contenido = base64.b64encode(Path(ruta_png).read_bytes()).decode()
    subido = _api(
        webhook, "disk.folder.uploadfile",
        id=carpeta["ID"],
        data={"NAME": Path(ruta_png).name},
        fileContent=contenido,
    )
    if not subido or not subido.get("ID"):
        raise RuntimeError("Bitrix no devolvio el id del archivo subido.")

    _api(webhook, "im.disk.file.commit",
         CHAT_ID=numero, UPLOAD_ID=subido["ID"], MESSAGE=mensaje or "")
    return subido["ID"]


def enviar_resumen(config, chrome_exe, chromedriver_exe, carpeta_salida):
    """Punto de entrada que usa balance.py. Nunca lanza: devuelve
    (ok, detalle) para que un fallo aqui no tire el chequeo de saldos,
    que a estas alturas ya termino y ya escribio en la hoja."""
    cfg = config.get("captura_bitrix") or {}
    if not cfg.get("activo", False):
        return False, "desactivado"

    webhook = (config.get("bitrix_webhook_url") or "").strip()
    if not webhook:
        return False, "no hay webhook de Bitrix configurado"

    chat_id = (cfg.get("chat_id") or "").strip()
    if not chat_id:
        return False, "no hay chat configurado"

    try:
        carpeta = Path(carpeta_salida)
        carpeta.mkdir(parents=True, exist_ok=True)
        destino = carpeta / f"saldos_{datetime.now():%Y%m%d_%H%M%S}.png"

        capturar_hoja(config.get("google_sheet_url", ""), chrome_exe, chromedriver_exe,
                      destino, cfg.get("recorte"))

        plantilla = cfg.get("mensaje") or "Saldos actualizados - {fecha}"
        mensaje = plantilla.replace("{fecha}", datetime.now().strftime("%d/%m/%Y %H:%M"))
        enviar_a_bitrix(webhook, chat_id, destino, mensaje)

        try:
            destino.unlink()        # ya esta en Bitrix; no se acumulan pngs
        except OSError:
            pass
        return True, f"captura enviada a {chat_id}"
    except Exception as e:
        logger.warning(f"No se pudo enviar la captura de saldos a Bitrix: {e}")
        return False, str(e)
