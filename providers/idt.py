from base_provider import BaseProvider
import time
import re
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException


class IDTProvider(BaseProvider):
    name = "IDT"
    sheet_row = 9
    sheet_col = 4

    config_fields = [
        {"key": "usuario", "label": "Usuario", "type": "str", "default": "colombiared2"},
        {"key": "password", "label": "Contraseña", "type": "str", "default": "LDfjHfol32!ceLmN"},
        {"key": "pet_answer", "label": "Respuesta mascota", "type": "str", "default": "Yankee"},
        {"key": "author_answer", "label": "Autor favorito", "type": "str", "default": "Eckhart Tollee"},
        {"key": "url", "label": "URL", "type": "str", "default": "https://secure.idtexpress.com/"},
    ]

    def _handle_security_questions(self, driver, pet_answer, author_answer):
        # *** Función idéntica a la del script original (handle_security_questions) ***
        answers_filled = 0
        last_input_used = None
        time.sleep(0.6)
        try:
            strong_candidates = driver.find_elements(By.XPATH, "//strong")
            q_elements = []
            for s in strong_candidates:
                try:
                    s.find_element(By.XPATH, "./ancestor::*[contains(@class,'text-left') or contains(@class,'control-label')]")
                    q_elements.append(s)
                    continue
                except Exception:
                    pass
                try:
                    lab = s.find_element(By.XPATH, "preceding::label[1]")
                    if 'question' in (lab.text or '').strip().lower():
                        q_elements.append(s)
                        continue
                except Exception:
                    pass

            for q_elem in q_elements:
                try:
                    q_text = (q_elem.text or "").strip().lower()
                except Exception:
                    q_text = ""

                answer_to_send = None
                if any(k in q_text for k in ("pet", "pet's", "pet name", "mascota", "nombre de tu mascota")):
                    answer_to_send = pet_answer
                elif any(k in q_text for k in ("author", "favorite author", "name of your favorite author", "autor", "autor favorito")):
                    answer_to_send = author_answer

                if not answer_to_send:
                    continue

                sent = False
                try:
                    input_elem = q_elem.find_element(By.XPATH, "following::input[1]")
                    input_elem.clear()
                    input_elem.send_keys(answer_to_send)
                    last_input_used = input_elem
                    sent = True
                except Exception:
                    pass

                if not sent:
                    try:
                        ta = q_elem.find_element(By.XPATH, "following::textarea[1]")
                        ta.clear()
                        ta.send_keys(answer_to_send)
                        last_input_used = ta
                        sent = True
                    except Exception:
                        pass

                if not sent:
                    try:
                        fallback = driver.find_element(By.NAME, "answer")
                        fallback.clear()
                        fallback.send_keys(answer_to_send)
                        last_input_used = fallback
                        sent = True
                    except Exception:
                        pass

                if sent:
                    answers_filled += 1

            if answers_filled > 0:
                try:
                    driver.find_element(By.NAME, "commit").click()
                except Exception:
                    try:
                        if last_input_used:
                            last_input_used.send_keys(Keys.ENTER)
                    except Exception:
                        pass
        except Exception:
            import traceback
            traceback.print_exc()
        return answers_filled

    def get_balance(self, config, google_sheet, sheet_url, driver_paths, get_driver_fn=None):
        chrome_exe = driver_paths["chrome_exe"]
        chromedriver_exe = driver_paths["chromedriver_exe"]

        driver = get_driver_fn(chrome_exe, chromedriver_exe, headless=True) if get_driver_fn else None
        if not driver:
            return False, "No se pudo crear el driver"

        TARGET_URL = config.get("url") or "https://secure.idtexpress.com/"
        try:
            driver.get(TARGET_URL)
            wait = WebDriverWait(driver, 40)

            # --- Login usuario ---
            try:
                user_input = wait.until(EC.presence_of_element_located((By.NAME, "user[login]")))
                user_input.clear()
                user_input.send_keys(config["usuario"])
                try:
                    driver.find_element(By.NAME, "commit").click()
                except Exception:
                    pass
                time.sleep(0.6)
            except TimeoutException:
                try:
                    user_input = wait.until(EC.presence_of_element_located((By.NAME, "username")))
                    user_input.clear()
                    user_input.send_keys(config["usuario"])
                    try:
                        driver.find_element(By.NAME, "commit").click()
                    except Exception:
                        pass
                    time.sleep(0.6)
                except Exception:
                    pass

            # --- Password ---
            pwd_input = None
            try:
                pwd_input = wait.until(EC.presence_of_element_located((By.NAME, "user[password]")))
            except Exception:
                try:
                    pwd_input = wait.until(EC.presence_of_element_located((By.NAME, "password")))
                except Exception:
                    pass

            if pwd_input:
                try:
                    pwd_input.clear()
                    pwd_input.send_keys(config["password"])
                    try:
                        driver.find_element(By.NAME, "commit").click()
                    except Exception:
                        pwd_input.send_keys(Keys.ENTER)
                    time.sleep(0.8)
                except Exception:
                    pass

            # --- Preguntas de seguridad ---
            try:
                filled = self._handle_security_questions(
                    driver,
                    config.get("pet_answer", "Yankee"),
                    config.get("author_answer", "Eckhart Tollee")
                )
                if filled:
                    print(f"IDT - Se llenaron {filled} preguntas de seguridad automáticamente.", flush=True)
                time.sleep(0.8)
            except Exception:
                import traceback
                traceback.print_exc()

            # --- Balance ---
            raw = wait.until(
                EC.presence_of_element_located(
                    (By.XPATH, "//div[contains(text(), 'Account Balance:')]/following-sibling::div//span")
                )
            ).text.strip()

            m = re.search(r"[-+]?\d[\d\.,]*", raw)
            if m:
                amt = float(m.group().replace(",", ""))
                formatted = f"$ {amt:.2f} USD"
            else:
                formatted = raw

            google_sheet.update_cell(self.sheet_row, self.sheet_col, formatted)
            return True, formatted

        except Exception as e:
            return False, str(e)
        finally:
            driver.quit()