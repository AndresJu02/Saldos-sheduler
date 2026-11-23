"""
Proveedor MyVoipTraffic - semiautomatico, al estilo de SipMovil/1980.

Por que no sirve el "proveedor generico" de la GUI para este sitio:

1. La pagina renderiza 20 copias del formulario de login; AngularJS
   (ngShowLogin) deja visibles solo 2: el widget de la cabecera (boton
   "Sign in", inputs con clase 'cookies-only') y el formulario principal
   (boton "Login", tiene input[name=page_referrer]). Hay que usar el
   principal y, sobre todo, el que este VISIBLE.

2. El formulario principal usa reCAPTCHA v3 invisible. El token lo inyecta
   el JS del sitio dentro de un setTimeout: medido el 2026-11-23, a los
   1000 ms NO existe y a los 3000 ms ya hay un token de ~2361 caracteres.
   Por eso se espera a que el input exista Y tenga valor: que el input
   falte significa "todavia no esta listo", nunca "no hay captcha".

   OJO con una suposicion que resulto FALSA: se penso que enviar sin
   'g-recaptcha-response' hacia que el sitio devolviera a /login, y que esa
   era la causa de los intentos fallidos. No lo es. El 2026-11-23 se
   comprobo con un login real que el token no llego en 45 s y aun asi el
   sitio acepto el login y entrego el saldo. El token mejora la puntuacion,
   no es obligatorio. Por eso aqui se espera, pero NUNCA se aborta por su
   ausencia: el exito real lo decide si la pagina sale de /login.

3. El aviso gris "Cookies are disabled" es un espejismo: #cookies-warning-msg
   viene siempre en el HTML del servidor y el JS lo oculta. Con cookies
   funcionando no hay ninguna capa que bloquee nada.

Perfil de Chrome propio y persistente (misma razon que IDT): reCAPTCHA v3
puntua mucho mejor un navegador con historial y cookies previas. Tras la
primera corrida -que conviene hacer a mano con "Probar ahora (visible)"-
la sesion queda guardada y las siguientes suelen entrar solas.
"""
import re
import sys
import time
from pathlib import Path

from base_provider import BaseProvider
from selenium.webdriver.common.by import By

try:
    from core.paths import BASE_DIR
except Exception:  # app no congelada o version anterior sin core.paths
    BASE_DIR = (Path(sys.executable).resolve().parent
                if getattr(sys, "frozen", False)
                else Path(__file__).resolve().parent.parent)

PERFIL_CHROME = BASE_DIR / "chrome_profile_myvoiptraffic"

URL_DEFECTO = "https://www.myvoiptraffic.com/login"

# Palabras que suelen acompanar al saldo en el panel, para el rastreo
# heuristico que se usa cuando no hay un XPath configurado a mano.
PISTAS_SALDO = ("balance", "credit", "saldo", "available", "funds")

RE_MONTO = r"[-+]?\d[\d\.,]*"


class MyVoipTrafficProvider(BaseProvider):
    name = "MyVoipTraffic"
    sheet_row = 2       # D2, igual que tenias en el proveedor generico
    sheet_col = 4

    config_fields = [
        {"key": "usuario", "label": "Usuario", "type": "str",
         "default": "colombiaredvoip_7821"},
        {"key": "password", "label": "Contrasena", "type": "str",
         "default": "MyVo1PTr4fF1Cc0Lr3d2027*"},
        {"key": "url", "label": "URL", "type": "str", "default": URL_DEFECTO},
        # Verificado 2026-11-23 con login real: redirige a /buy_credit2/ y el
        # saldo esta en <span class="balance">, igual que SipTraffic.
        {"key": "balance_xpath", "label": "XPath del saldo (opcional)",
         "type": "str", "default": "//span[@class='balance']"},
        {"key": "espera_manual", "label": "Segundos para intervenir a mano",
         "type": "str", "default": "180"},
    ]

    # ------------------------------------------------------------------
    # Utilidades
    # ------------------------------------------------------------------
    def _form_visible(self, driver):
        """Devuelve el <form> de login visible correcto.

        Criterio, en orden: que tenga el contenedor .g-recaptcha (es el
        formulario principal en ambas marcas), luego page_referrer, y por
        ultimo el primero visible que tenga campo de usuario.

        Ojo: page_referrer NO sirve como unico criterio. En SipTraffic -la
        misma plataforma- los 20 formularios lo llevan, y solo .g-recaptcha
        distingue el principal del widget de cabecera."""
        forms = [f for f in driver.find_elements(By.TAG_NAME, "form")
                 if f.is_displayed() and f.find_elements(By.NAME, "login[username]")]
        for f in forms:
            if f.find_elements(By.CSS_SELECTOR, ".g-recaptcha"):
                return f
        for f in forms:
            if f.find_elements(By.NAME, "page_referrer"):
                return f
        return forms[0] if forms else None

    def _esperar_token_recaptcha(self, driver, form, timeout=15):
        """Espera a que el JS del sitio inyecte g-recaptcha-response con valor.
        Devuelve (ok, detalle). Si el form no tiene contenedor .g-recaptcha,
        asume que ese formulario no lo usa."""
        if not form.find_elements(By.CSS_SELECTOR, ".g-recaptcha"):
            return True, "sin reCAPTCHA en este formulario"

        t0 = time.time()
        fin = t0 + timeout
        while time.time() < fin:
            try:
                els = form.find_elements(By.NAME, "g-recaptcha-response")
                if els and len((els[0].get_attribute("value") or "")) > 20:
                    return True, "token listo en %d ms" % int((time.time() - t0) * 1000)
            except Exception:
                pass
            time.sleep(0.25)
        return False, "el token de reCAPTCHA no aparecio en %d s" % timeout

    def _leer_saldo(self, driver, xpath_cfg):
        """Lee el saldo. Intenta primero el XPath configurado y, si no da
        nada, cae a rastrear el texto de la pagina buscando un monto junto a
        palabras tipo 'balance'. El reintento lo pone el bucle de get_balance,
        por eso aqui no se espera: se mira lo que haya en este instante."""
        if xpath_cfg:
            try:
                for el in driver.find_elements(By.XPATH, xpath_cfg):
                    txt = (el.text or "").strip()
                    # Exigir un numero: un XPath algo laxo tambien engancha la
                    # etiqueta ("Your credit:"), que no sirve de saldo.
                    if txt and re.search(RE_MONTO, txt):
                        return txt, "xpath configurado"
            except Exception:
                pass    # XPath malo o elemento aun no presente: va la heuristica

        # Heuristica: elementos hoja cuyo texto tenga una pista y un monto.
        candidatos = []
        for el in driver.find_elements(By.XPATH, "//*[not(self::script or self::style)]"):
            try:
                if el.find_elements(By.XPATH, "./*"):
                    continue           # solo hojas, para no leer el body entero
                txt = (el.text or "").strip()
            except Exception:
                continue
            if not txt or len(txt) > 120:
                continue
            bajo = txt.lower()
            if any(p in bajo for p in PISTAS_SALDO) and re.search(RE_MONTO, txt):
                candidatos.append(txt)
            elif re.fullmatch(r"[\$€]?\s*" + RE_MONTO + r"\s*(USD|EUR|COP)?", txt):
                try:
                    ctx = (el.find_element(By.XPATH, "..").text or "").lower()
                    if any(p in ctx for p in PISTAS_SALDO):
                        candidatos.append(txt)
                except Exception:
                    pass
        if candidatos:
            return candidatos[0], "heuristica (%d candidatos)" % len(candidatos)
        raise RuntimeError(
            "No se encontro el saldo. Configura 'XPath del saldo' en la "
            "ficha del proveedor con el elemento correcto del panel."
        )

    def _formatear(self, crudo):
        m = re.search(RE_MONTO, crudo or "")
        if not m:
            return (crudo or "").strip()
        try:
            val = float(m.group().replace(",", ""))
            return "$ %.2f USD" % val
        except ValueError:
            return crudo.strip()

    # ------------------------------------------------------------------
    # Entrada principal
    # ------------------------------------------------------------------
    def get_balance(self, config, google_sheet, sheet_url, driver_paths,
                    get_driver_fn=None, headless=True):
        chrome_exe = driver_paths["chrome_exe"]
        chromedriver_exe = driver_paths["chromedriver_exe"]

        # Siempre visible: igual que SipMovil/1980, este proveedor esta
        # pensado para que puedas intervenir si reCAPTCHA se pone dificil.
        PERFIL_CHROME.mkdir(parents=True, exist_ok=True)
        try:
            driver = get_driver_fn(chrome_exe, chromedriver_exe, headless=False,
                                   user_data_dir=str(PERFIL_CHROME)) if get_driver_fn else None
        except TypeError:
            # Version de get_driver_fn que aun no acepta user_data_dir.
            driver = get_driver_fn(chrome_exe, chromedriver_exe,
                                   headless=False) if get_driver_fn else None
        if not driver:
            return False, "No se pudo crear el driver"

        url = config.get("url") or URL_DEFECTO
        try:
            espera_manual = float(config.get("espera_manual") or 180)
        except (TypeError, ValueError):
            espera_manual = 180.0
        xpath_cfg = (config.get("balance_xpath") or "").strip()

        try:
            driver.get(url)
            time.sleep(2.0)

            # --- El perfil ya traia sesion abierta? ----------------------
            ya_dentro = "/login" not in (driver.current_url or "")

            if not ya_dentro:
                form = None
                fin = time.time() + 30
                while time.time() < fin and form is None:
                    form = self._form_visible(driver)
                    if form is None:
                        time.sleep(0.5)
                if form is None:
                    return False, ("No aparecio ningun formulario de login visible. "
                                   "Revisa que no haya un bloqueador de cookies.")

                usuario = config.get("usuario", "")
                password = config.get("password", "")
                if not usuario or not password:
                    falta = "usuario" if not usuario else "contrasena"
                    return False, (
                        "Falta la %s. En la pestana Proveedores selecciona "
                        "MyVoipTraffic, pulsa 'Configurar', llena el campo y "
                        "pulsa 'Guardar'." % falta
                    )

                u = form.find_element(By.NAME, "login[username]")
                u.clear()
                u.send_keys(usuario)
                p = form.find_element(By.NAME, "login[password]")
                p.clear()
                p.send_keys(password)

                # --- El paso que lo cambia todo -------------------------
                # Esperar el token mejora la puntuacion de reCAPTCHA v3 y no
                # cuesta nada cuando llega rapido (1-3 s). Pero NO se aborta si
                # no llega: comprobado el 2026-11-23 que el sitio acepta el
                # login igual sin 'g-recaptcha-response'. El exito real lo
                # decide el bucle de mas abajo, no este token.
                ok_token, detalle = self._esperar_token_recaptcha(driver, form)
                print("MyVoipTraffic - reCAPTCHA: " + detalle +
                      ("" if ok_token else " (se envia igual)"), flush=True)

                form.find_element(By.CSS_SELECTOR, "input[type=submit]").click()

            # --- Espera semiautomatica -------------------------------------
            # Igual que SipMovil: la ventana esta a la vista; si el sitio pide
            # algo (reCAPTCHA v2, verificacion, clave vencida) lo resuelves a
            # mano y el bucle sigue solo en cuanto detecta el saldo.
            print("MyVoipTraffic - Si el sitio pide algo, resuelvelo en la "
                  "ventana. Esperando hasta %d s..." % int(espera_manual), flush=True)

            fin = time.time() + espera_manual
            crudo = None
            origen = ""
            while time.time() < fin:
                if "/login" not in (driver.current_url or ""):
                    try:
                        crudo, origen = self._leer_saldo(driver, xpath_cfg)
                        break
                    except Exception:
                        pass
                time.sleep(1.0)

            if crudo is None:
                try:
                    self.save_debug_snapshot(driver, "sin_saldo")
                except Exception:
                    pass
                return False, ("No se pudo leer el saldo dentro del tiempo de espera. "
                               "Si la sesion si abrio, configura el XPath del saldo.")

            formateado = self._formatear(crudo)
            print("MyVoipTraffic - saldo leido por %s: %r" % (origen, crudo), flush=True)

            if google_sheet is not None:
                google_sheet.update_cell(self.sheet_row, self.sheet_col, formateado)
            return True, formateado

        except Exception as e:
            try:
                self.save_debug_snapshot(driver, "excepcion")
            except Exception:
                pass
            return False, str(e)
        finally:
            try:
                driver.quit()
            except Exception:
                pass
