from base_provider import BaseProvider
import time
import re
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

class DataVoiceProvider(BaseProvider):
    name = "DataVoice"
    sheet_row = 7
    sheet_col = 4

    config_fields = [
        {"key": "usuario", "label": "Usuario", "type": "str", "default": "colombiaredvozip"},
        {"key": "password", "label": "Contraseña", "type": "str", "default": "colombiaredvozip"},
        {"key": "url", "label": "URL", "type": "str", "default": "http://clientes.datavoice.com.co/Callshop/Login?ReturnUrl=%2fCallshop%2f"},
    ]

    def get_balance(self, config, google_sheet, sheet_url, driver_paths, get_driver_fn=None):
        chrome_exe = driver_paths["chrome_exe"]
        chromedriver_exe = driver_paths["chromedriver_exe"]

        driver = get_driver_fn(chrome_exe, chromedriver_exe, headless=True) if get_driver_fn else None
        if not driver:
            return False, "No se pudo crear el driver"

        try:
            driver.get(config.get("url") or "http://clientes.datavoice.com.co/Callshop/Login?ReturnUrl=%2fCallshop%2f")
            # Bypass interstitial (llamar a la función bypass del original si existe)
            # Si no, puedes incluirla aquí mismo.

            WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.NAME, "UserName"))).send_keys(config["usuario"])
            driver.find_element(By.NAME, "Password").send_keys(config["password"])
            driver.find_element(By.NAME, "submit").click()
            time.sleep(4)

            raw = WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.XPATH, "//div[contains(@style,'margin')]/p[contains(text(), '$')]"))
            ).text.strip()

            m = re.search(r"[-+]?\d[\d\.,]*", raw)
            if m:
                amt = float(m.group().replace(",", ""))
                formatted = f"$ {int(round(amt))} USD"
            else:
                formatted = raw

            google_sheet.update_cell(self.sheet_row, self.sheet_col, formatted)
            return True, formatted

        except Exception as e:
            return False, str(e)
        finally:
            driver.quit()