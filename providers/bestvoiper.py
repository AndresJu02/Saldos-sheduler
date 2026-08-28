from base_provider import BaseProvider
import re
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

class BestVoIPerProvider(BaseProvider):
    name = "BestVoIPer"
    sheet_row = 7
    sheet_col = 2

    config_fields = [
        {"key": "usuario", "label": "Usuario", "type": "str", "default": "COLOMBIARED"},
        {"key": "password", "label": "Contraseña", "type": "str", "default": "Bestvoiper2023"},
        {"key": "url", "label": "URL", "type": "str", "default": "https://sw4.bestvoiper.com/reporteria/ingreso"},
    ]

    def get_balance(self, config, google_sheet, sheet_url, driver_paths, get_driver_fn=None, headless=True):
        chrome_exe = driver_paths["chrome_exe"]
        chromedriver_exe = driver_paths["chromedriver_exe"]

        driver = get_driver_fn(chrome_exe, chromedriver_exe, headless=headless) if get_driver_fn else None
        if not driver:
            return False, "No se pudo crear el driver"

        BESTVOIPER_URL = config.get("url") or "https://sw4.bestvoiper.com/reporteria/ingreso"
        try:
            wait = WebDriverWait(driver, 40)
            driver.get(BESTVOIPER_URL)

            wait.until(EC.presence_of_element_located((By.NAME, "ingUsuario"))).send_keys(config["usuario"])
            driver.find_element(By.NAME, "ingPassword").send_keys(config["password"])
            wait.until(EC.element_to_be_clickable((By.XPATH, "//button[@type='submit']"))).click()

            saldo_el = wait.until(EC.visibility_of_element_located((By.XPATH, "//div[@id='saldo']//h6")))
            raw = saldo_el.text.strip()

            # ------------------------------------------------------------
            # Extraemos la cadena numérica y la formateamos DIRECTAMENTE,
            # sin float(), para no perder decimales ni agregar redondeos.
            # ------------------------------------------------------------
            m = re.search(r"[\d\.,]+", raw)
            if m:
                numero_str = m.group()
                # Si hay punto y coma, asumimos que el punto es separador de miles
                if '.' in numero_str and ',' in numero_str:
                    # Eliminamos los puntos de miles y convertimos la coma a punto
                    numero_str = numero_str.replace('.', '').replace(',', '.')
                elif ',' in numero_str and '.' not in numero_str:
                    # Puede ser miles: si después de la coma hay 3 dígitos, es miles; si no, es decimal
                    partes = numero_str.split(',')
                    if len(partes) == 2 and len(partes[1]) == 3:
                        # es separador de miles → eliminar la coma
                        numero_str = numero_str.replace(',', '')
                    else:
                        # es coma decimal → cambiar a punto
                        numero_str = numero_str.replace(',', '.')
                # Ahora tenemos un número con punto decimal (si corresponde)
                # Mostramos el número exactamente como está, SIN redondear
                formatted = f"$ {numero_str} COP"
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