from base_provider import BaseProvider
from .vos_helpers import run_site

class MORSipMovilProvider(BaseProvider):
    name = "SipMovil"
    sheet_row = 8
    sheet_col = 4

    config_fields = [
        {"key": "usuario", "label": "Número", "type": "str", "default": "576017942720"},
        {"key": "password", "label": "Contraseña", "type": "str", "default": "65741274"},
        {"key": "url", "label": "URL", "type": "str", "default": "http://45.226.115.82:8080/eng/index.html"},
    ]

    def get_balance(self, config, google_sheet, sheet_url, driver_paths, get_driver_fn=None):
        chrome_exe = driver_paths["chrome_exe"]
        chromedriver_exe = driver_paths["chromedriver_exe"]

        driver = get_driver_fn(chrome_exe, chromedriver_exe, headless=False) if get_driver_fn else None
        if not driver:
            return False, "No se pudo crear el driver"

        site_cfg = {
            "url": config.get("url") or "http://45.226.115.82:8080/eng/index.html",
            "login_type_text": "Phone",
            "number": config["usuario"],
            "password": config["password"],
        }

        try:
            run_site(driver, site_cfg, google_sheet, self.sheet_row)
            return True, "Saldo guardado"
        except Exception as e:
            detalle = str(e).strip()
            return False, f"{type(e).__name__}: {detalle}" if detalle else type(e).__name__
        finally:
            # Con try/except: si cerrar el navegador falla (Chrome colgado,
            # driver ya muerto), esa excepcion reemplazaria el valor que
            # devuelve get_balance y convertiria un saldo ya guardado en un
            # fallo.
            try:
                driver.quit()
            except Exception:
                pass