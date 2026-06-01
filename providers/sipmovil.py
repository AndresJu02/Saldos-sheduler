from base_provider import BaseProvider
from .vos_helpers import run_site

class MORSipMovilProvider(BaseProvider):
    name = "SipMovil"
    sheet_row = 8
    sheet_col = 4

    config_fields = [
        {"key": "usuario", "label": "Número", "type": "str", "default": "576017942720"},
        {"key": "password", "label": "Contraseña", "type": "str", "default": "65741274"},
    ]

    def get_balance(self, config, google_sheet, sheet_url, driver_paths, get_driver_fn=None):
        chrome_exe = driver_paths["chrome_exe"]
        chromedriver_exe = driver_paths["chromedriver_exe"]

        driver = get_driver_fn(chrome_exe, chromedriver_exe, headless=False) if get_driver_fn else None
        if not driver:
            return False, "No se pudo crear el driver"

        site_cfg = {
            "url": "http://45.226.115.82:8080/eng/index.html",
            "login_type_text": "Phone",
            "number": config["usuario"],
            "password": config["password"],
        }

        try:
            run_site(driver, site_cfg, google_sheet, self.sheet_row)
            return True, "Saldo guardado"
        except Exception as e:
            return False, str(e)
        finally:
            driver.quit()