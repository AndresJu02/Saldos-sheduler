from base_provider import BaseProvider
import re
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

class CityphoneProvider(BaseProvider):
    name = "Cityphone"
    sheet_row = 6
    sheet_col = 4

    config_fields = [
        {"key": "usuario", "label": "Usuario", "type": "str", "default": "redcolombia"},
        {"key": "password", "label": "Contraseña", "type": "str", "default": "c1tyipC0lreeD@"},
        {"key": "url", "label": "URL", "type": "str", "default": "https://www.voip-llamada.com/voip/"},
    ]

    def get_balance(self, config, google_sheet, sheet_url, driver_paths, get_driver_fn=None, headless=True):
        chrome_exe = driver_paths["chrome_exe"]
        chromedriver_exe = driver_paths["chromedriver_exe"]

        driver = get_driver_fn(chrome_exe, chromedriver_exe, headless=headless) if get_driver_fn else None
        if not driver:
            return False, "No se pudo crear el driver"

        try:
            driver.get(config.get("url") or "https://www.voip-llamada.com/voip/")
            try:
                WebDriverWait(driver, 5).until(EC.element_to_be_clickable((By.NAME, "acepto"))).click()
            except Exception:
                pass

            WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.NAME, "user"))).send_keys(config["usuario"])
            driver.find_element(By.NAME, "pass").send_keys(config["password"])
            driver.find_element(By.NAME, "Submit").click()

            raw = WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "#saldo b"))
            ).text.strip()

            # ------------------------------------------------------------
            # Cityphone muestra algo como "204.665,75" (miles con punto, decimales con coma).
            # Nos quedamos con la parte entera y formateamos con punto como separador de miles.
            # ------------------------------------------------------------
            # Buscar la primera aparición de un número con posible formato
            m = re.search(r"[\d\.,]+", raw)
            if m:
                token = m.group()
                # Separar en parte entera y decimal (la coma indica decimales)
                if ',' in token:
                    entero_str = token.split(',')[0]  # lo que está antes de la coma
                else:
                    entero_str = token  # no hay decimales
                # Limpiar puntos de miles si los hay, porque los vamos a re-aplicar
                entero_limpio = entero_str.replace('.', '')
                # Convertir a entero para formatear con puntos de miles
                try:
                    valor = int(entero_limpio)
                    # Formato colombiano: puntos para miles, sin decimales
                    formatted = f"$ {valor:,}".replace(",", ".")
                except ValueError:
                    formatted = raw
            else:
                formatted = raw

            if google_sheet is not None:
                google_sheet.update_cell(self.sheet_row, self.sheet_col, formatted)
            return True, formatted

        except Exception as e:
            try:
                self.save_debug_snapshot(driver, "excepcion")
            except Exception:
                pass
            return False, str(e)
        finally:
            driver.quit()