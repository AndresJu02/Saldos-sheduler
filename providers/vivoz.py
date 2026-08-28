from base_provider import BaseProvider
import time
import re
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

class VivozProvider(BaseProvider):
    name = "Vivoz"
    sheet_row = 6
    sheet_col = 2

    config_fields = [
        {"key": "usuario", "label": "Usuario", "type": "str", "default": "colombiared"},
        {"key": "password", "label": "Contraseña", "type": "str", "default": "v1v0zzc0lrEd@"},
        {"key": "url", "label": "URL", "type": "str", "default": "http://178.105.24.84/billing/"},
    ]

    def get_balance(self, config, google_sheet, sheet_url, driver_paths, get_driver_fn=None, headless=True):
        chrome_exe = driver_paths["chrome_exe"]
        chromedriver_exe = driver_paths["chromedriver_exe"]

        # Usar la función robusta (si se proporcionó) o crear un driver simple
        if get_driver_fn:
            driver = get_driver_fn(chrome_exe, chromedriver_exe, headless=headless)
        else:
            # Fallback mínimo si no se pasó la función (no recomendado)
            from selenium import webdriver
            from selenium.webdriver.chrome.service import Service
            from selenium.webdriver.chrome.options import Options
            opts = Options()
            opts.binary_location = chrome_exe
            if headless:
                opts.add_argument("--headless=new")
            opts.add_argument("--no-sandbox")
            service = Service(executable_path=chromedriver_exe)
            driver = webdriver.Chrome(service=service, options=opts)

        try:
            driver.get(config.get("url") or "http://178.105.24.84/billing/")

            # Bypass interstitial HTTP inseguro (como en el original)
            bypass_insecure_interstitial(driver)

            # Login
            WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.NAME, "login[username]"))
            ).send_keys(config["usuario"])
            driver.find_element(By.NAME, "login[psw]").send_keys(config["password"])
            driver.find_element(By.NAME, "commit").click()

            # Esperar Quick Stats (el panel puede cargar en inglés o en español,
            # por eso comprobamos por ID -que no cambia con el idioma- en vez
            # de por el texto del título).
            try:
                WebDriverWait(driver, 5).until(
                    EC.element_to_be_clickable((By.ID, "qs_refresh"))
                )
            except TimeoutException:
                pass

            # Refrescar si es necesario
            try:
                driver.execute_script("document.getElementById('qs_refresh')?.click();")
            except Exception:
                pass

            # Esperar a que aparezca el saldo, en cualquiera de los dos idiomas
            # en los que el panel puede renderizarse ("Balance:" o "Saldo:").
            def _saldo_visible(d):
                try:
                    txt = d.find_element(By.ID, "show_quick_stats").text
                except Exception:
                    return False
                return bool(re.search(r"balance|saldo", txt, re.IGNORECASE))

            try:
                WebDriverWait(driver, 10).until(
                    EC.presence_of_element_located((By.ID, "show_quick_stats"))
                )
                WebDriverWait(driver, 40).until(_saldo_visible)
            except TimeoutException:
                pass

            # Intentar extraer el balance con los mismos localizadores del script
            # original, repetidos para las dos etiquetas posibles: "Balance" (inglés)
            # y "Saldo" (español) -el panel alterna entre los dos idiomas-.
            raw_text = None
            candidates = [
                (By.XPATH, 'id("quick_stats")/tbody[1]/tr[12]/td[2]'),
            ]
            for etiqueta in ("Balance", "Saldo"):
                candidates += [
                    (By.XPATH, f"//td[normalize-space(text())='{etiqueta}:']/following-sibling::td"),
                    (By.XPATH, f"//*[contains(normalize-space(text()),'{etiqueta}')]/following::td[1]"),
                    (By.XPATH, f"//b[contains(normalize-space(text()),'{etiqueta}')]/ancestor::td/following-sibling::td[1]"),
                    (By.XPATH, f"//td[contains(.,'{etiqueta}')]/following-sibling::td[1]"),
                ]
            for _ in range(2):  # reintento
                for locator in candidates:
                    try:
                        el = WebDriverWait(driver, 5).until(EC.presence_of_element_located(locator))
                        raw_text = driver.execute_script("return arguments[0].textContent;", el) or el.text
                        raw_text = raw_text.strip()
                        # Validamos que el texto realmente parezca un saldo numérico
                        # (evita que un candidato "atrape" una celda vacía o irrelevante,
                        # algo más probable con el XPath posicional fijo).
                        if raw_text and re.search(r"\d", raw_text):
                            break
                        raw_text = None
                    except TimeoutException:
                        continue
                if raw_text:
                    break
                # Si no encontró, refrescar y reintentar
                try:
                    btn = WebDriverWait(driver, 3).until(EC.element_to_be_clickable((By.ID, "qs_refresh")))
                    btn.click()
                    time.sleep(1.0)
                except Exception:
                    time.sleep(0.5)

            if not raw_text:
                # Último recurso antes del fallback HTTP: escanear todo el texto
                # del panel Quick Stats en busca de "Balance ... <número>", sin
                # depender de la posición exacta de filas/columnas. Más resistente
                # a que la tabla cambie de forma por avisos/promos/alertas.
                try:
                    panel_text = driver.execute_script(
                        "var p = document.getElementById('quick_stats') || "
                        "document.getElementById('show_quick_stats'); "
                        "return p ? p.innerText : '';"
                    ) or ""
                    match = re.search(r"(?:Balance|Saldo)[^\d\-+]{0,20}([-+]?\d[\d\.,]*)", panel_text, re.IGNORECASE)
                    if match:
                        raw_text = match.group(0).strip()
                except Exception:
                    pass

            if not raw_text:
                # Fallback vía HTTP (como en el original)
                try:
                    import requests
                    session = requests.Session()
                    for cookie in driver.get_cookies():
                        session.cookies.set(cookie['name'], cookie.get('value'), domain=cookie.get('domain') or '178.105.24.84')
                    resp = session.get("http://178.105.24.84/billing/callc/main_quick_stats", timeout=15)
                    match = re.search(r'(?:Balance|Saldo):</td>\s*<td[^>]*>([^<]+)', resp.text, re.IGNORECASE)
                    if match:
                        raw_text = match.group(1).strip()
                except Exception:
                    pass

            if not raw_text:
                self.save_debug_snapshot(driver, "sin_balance")
                return False, "No se encontró el balance"

            # Formatear
            m = re.search(r"[-+]?\d[\d\.,]*", raw_text)
            if m:
                amount = float(m.group().replace(",", ""))
                formatted = f"$ {amount:.2f} USD"
            else:
                formatted = raw_text

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


def bypass_insecure_interstitial(driver, timeout=5):
    """Copia exacta de la función del script original."""
    try:
        result = driver.execute_script(
            "return document.querySelector('#details-button') !== null || "
            "document.querySelector('#proceed-link') !== null || "
            "document.querySelector('button#proceed-button') !== null;"
        )
        if result:
            driver.execute_script(
                "var d = document.querySelector('#details-button'); if(d) d.click();"
            )
            time.sleep(0.4)
            driver.execute_script(
                "var p = document.querySelector('#proceed-link'); if(p) p.click();"
            )
            time.sleep(0.8)
            return
    except Exception:
        pass
    # Fallback Selenium
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.common.exceptions import TimeoutException
    try:
        btn = WebDriverWait(driver, timeout).until(EC.element_to_be_clickable((By.ID, "details-button")))
        btn.click()
        time.sleep(0.4)
        proceed = WebDriverWait(driver, 5).until(EC.element_to_be_clickable((By.ID, "proceed-link")))
        proceed.click()
        time.sleep(0.8)
    except TimeoutException:
        pass