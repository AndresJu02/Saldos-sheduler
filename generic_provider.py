"""
Proveedor genérico configurable desde la GUI (sin necesidad de escribir código).

Cada proveedor "genérico" se guarda como un diccionario dentro de
scheduler_config.json -> "custom_providers", y esta clase sabe interpretar
esa definición para: abrir la URL, llenar usuario/contraseña, hacer clic en
"enviar" (o pulsar ENTER), esperar, y leer el texto del saldo con un selector
+ una expresión regular opcional.

No sirve para sitios con captcha, múltiples iframes anidados, o flujos de
varios pasos (para esos casos se sigue necesitando un archivo .py a medida
dentro de la carpeta 'providers', como los ya existentes).
"""
import time
import re
from base_provider import BaseProvider
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

BY_MAP = {
    "name": By.NAME,
    "id": By.ID,
    "css": By.CSS_SELECTOR,
    "xpath": By.XPATH,
}

DEFAULT_BALANCE_REGEX = r"[-+]?\d[\d\.,]*"


def _switch_root(driver):
    try:
        driver.switch_to.default_content()
    except Exception:
        pass


def find_in_iframes(driver, by, value, timeout=15, clickable=False):
    """
    Busca un elemento primero en el documento principal y, si no aparece,
    entra en cada <iframe> (un nivel) a buscarlo ahí. Muchos paneles de
    facturación (PortaOne/VOS3000 y similares) cargan la info dentro de un
    iframe, así que esto evita tener que saberlo de antemano.
    Deja el foco (driver) posicionado dentro del iframe donde encontró el
    elemento, para que se pueda seguir interactuando ahí (ej. leer el saldo
    justo después de hacer login en ese mismo iframe).
    """
    _switch_root(driver)
    wait = WebDriverWait(driver, timeout)
    cond = EC.element_to_be_clickable((by, value)) if clickable else EC.presence_of_element_located((by, value))
    try:
        el = wait.until(cond)
        return el
    except TimeoutException:
        pass

    iframes = driver.find_elements(By.TAG_NAME, "iframe")
    for fr in iframes:
        _switch_root(driver)
        try:
            driver.switch_to.frame(fr)
        except Exception:
            continue
        try:
            el = WebDriverWait(driver, 5).until(cond)
            return el
        except TimeoutException:
            continue

    _switch_root(driver)
    raise TimeoutException(f"No se encontró el elemento ({by}='{value}') ni en la página ni en sus iframes.")


class GenericWebProvider(BaseProvider):
    """
    cfg (definición del sitio, viene de custom_providers) espera claves:
      name, url, timeout,
      user_selector_type, user_selector,
      pass_selector_type, pass_selector,
      submit_selector_type, submit_selector   (opcional; si falta, se usa ENTER)
      wait_after_login,
      balance_selector_type, balance_selector,
      balance_regex   (opcional),
      prefix, suffix  (opcional, para formatear el resultado)
      sheet_row, sheet_col,
      usuario_default, password_default
    """

    def __init__(self, cfg=None):
        cfg = dict(cfg or {})
        self._cfg = cfg
        self.name = str(cfg.get("name") or "Proveedor genérico")
        try:
            self.sheet_row = int(cfg.get("sheet_row", 1))
        except (TypeError, ValueError):
            self.sheet_row = 1
        try:
            self.sheet_col = int(cfg.get("sheet_col", 1))
        except (TypeError, ValueError):
            self.sheet_col = 1

        self.config_fields = [
            {"key": "usuario", "label": "Usuario", "type": "str",
             "default": cfg.get("usuario_default", "")},
            {"key": "password", "label": "Contraseña", "type": "str",
             "default": cfg.get("password_default", "")},
        ]

    def get_balance(self, config, google_sheet, sheet_url, driver_paths, get_driver_fn=None, headless=True):
        cfg = self._cfg
        chrome_exe = driver_paths["chrome_exe"]
        chromedriver_exe = driver_paths["chromedriver_exe"]

        driver = get_driver_fn(chrome_exe, chromedriver_exe, headless=headless) if get_driver_fn else None
        if not driver:
            return False, "No se pudo crear el driver"

        try:
            url = cfg.get("url", "")
            if not url:
                return False, "No se configuró la URL del sitio."

            driver.get(url)
            timeout = float(cfg.get("timeout", 30) or 30)

            # --- Usuario (busca en la página y, si no, dentro de iframes) ---
            by_user = BY_MAP.get(cfg.get("user_selector_type", "name"), By.NAME)
            user_el = find_in_iframes(driver, by_user, cfg["user_selector"], timeout=timeout)
            user_el.clear()
            user_el.send_keys(config.get("usuario", ""))

            # --- Contraseña (se busca en el mismo contexto donde quedó el usuario) ---
            by_pass = BY_MAP.get(cfg.get("pass_selector_type", "name"), By.NAME)
            pass_el = WebDriverWait(driver, timeout).until(
                EC.presence_of_element_located((by_pass, cfg["pass_selector"]))
            )
            pass_el.clear()
            pass_el.send_keys(config.get("password", ""))

            # --- Enviar ---
            submit_selector = cfg.get("submit_selector", "")
            if submit_selector:
                by_submit = BY_MAP.get(cfg.get("submit_selector_type", "css"), By.CSS_SELECTOR)
                driver.find_element(by_submit, submit_selector).click()
            else:
                pass_el.send_keys(Keys.ENTER)

            time.sleep(float(cfg.get("wait_after_login", 1.5) or 1.5))

            # --- Leer saldo (de nuevo: página principal y, si no, iframes) ---
            by_balance = BY_MAP.get(cfg.get("balance_selector_type", "xpath"), By.XPATH)
            balance_el = find_in_iframes(driver, by_balance, cfg["balance_selector"], timeout=timeout)
            raw = (balance_el.text or "").strip()

            if not raw:
                return False, ("El selector de saldo encontró un elemento vacío. "
                               "Revisa que el XPath/CSS apunte exactamente a la celda con el número "
                               "(no a una celda vecina vacía).")

            pattern = cfg.get("balance_regex") or DEFAULT_BALANCE_REGEX
            m = re.search(pattern, raw)
            if not m:
                return False, (f"El texto encontrado fue '{raw}' pero el regex de extracción "
                                "no encontró ningún número dentro de él.")
            valor = m.group()

            formatted = f"{cfg.get('prefix', '')}{valor}{cfg.get('suffix', '')}".strip()
            if google_sheet is not None:
                google_sheet.update_cell(self.sheet_row, self.sheet_col, formatted)
            return True, formatted

        except Exception as e:
            return False, str(e)
        finally:
            try:
                driver.quit()
            except Exception:
                pass