from base_provider import BaseProvider
from .vos_helpers import run_site

class MOR1980Provider(BaseProvider):
    name = "1980"
    sheet_row = 4
    sheet_col = 4

    config_fields = [
        {"key": "usuario", "label": "Usuario", "type": "str", "default": "Colombiared-v1"},
        {"key": "password", "label": "Contraseña", "type": "str", "default": "tBanbMgj"},
        {"key": "url", "label": "URL", "type": "str", "default": "http://158.69.177.101:8148/customer/eng/index.html"},
    ]

    def get_balance(self, config, google_sheet, sheet_url, driver_paths, get_driver_fn=None):
        chrome_exe = driver_paths["chrome_exe"]
        chromedriver_exe = driver_paths["chromedriver_exe"]

        driver = get_driver_fn(chrome_exe, chromedriver_exe, headless=False) if get_driver_fn else None
        if not driver:
            return False, "No se pudo crear el driver"

        site_cfg = {
            "url": config.get("url") or "http://158.69.177.101:8148/customer/eng/index.html",
            "login_type_text": "Mapping Gateway",
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