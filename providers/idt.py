from base_provider import BaseProvider
import time
import re
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

from core.paths import BASE_DIR

# Perfil de Chrome propio y persistente para IDT (ver get_balance): un
# Chrome "nuevo" sin cookies en cada corrida hace que IDT lo trate como
# dispositivo desconocido y muestre su verificación anti-bot ("Anomaly
# Detected"), aunque el login manual del usuario -que sí tiene cookies de
# sesiones anteriores- nunca la vea. Reutilizando siempre esta misma
# carpeta, después de la primera vez (que puede seguir pidiendo esa
# verificación, y hay que resolverla a mano una vez con headless=False)
# el dispositivo queda reconocido para las corridas automáticas de ahí en
# más, igual que el navegador normal de un usuario real.
PERFIL_CHROME_IDT = BASE_DIR / "chrome_profile_idt"


def _escribir_lento(elemento, texto, duracion_seg=6.0):
    """Escribe `texto` caracter por caracter repartiendo `duracion_seg` entre
    todos, en vez de mandarlo de una con send_keys(texto) -que Selenium
    manda casi instantáneo-. Se usa para la contraseña de IDT porque el
    sitio la rechazaba/marcaba como sospechosa cuando llegaba de golpe."""
    if not texto:
        elemento.send_keys(texto)
        return
    demora = duracion_seg / len(texto)
    for caracter in texto:
        elemento.send_keys(caracter)
        time.sleep(demora)


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

    def get_balance(self, config, google_sheet, sheet_url, driver_paths, get_driver_fn=None, headless=True):
        chrome_exe = driver_paths["chrome_exe"]
        chromedriver_exe = driver_paths["chromedriver_exe"]

        PERFIL_CHROME_IDT.mkdir(parents=True, exist_ok=True)
        try:
            driver = get_driver_fn(
                chrome_exe, chromedriver_exe, headless=headless, user_data_dir=str(PERFIL_CHROME_IDT)
            ) if get_driver_fn else None
        except TypeError:
            # get_driver_fn de alguna versión anterior que todavía no acepta
            # user_data_dir -no debería pasar en esta app, pero así no se
            # rompe get_balance() por completo si algún día se llama con
            # una función distinta que no lo soporte-.
            driver = get_driver_fn(chrome_exe, chromedriver_exe, headless=headless) if get_driver_fn else None
        if not driver:
            return False, "No se pudo crear el driver"

        TARGET_URL = config.get("url") or "https://secure.idtexpress.com/"
        try:
            driver.get(TARGET_URL)
            wait = WebDriverWait(driver, 40)

            def _pagina_cargo_bien(espera):
                """True si esta pantalla trae el login o ya el saldo -o sea,
                no es una pantalla intermedia rota-."""
                try:
                    WebDriverWait(driver, espera).until(lambda d: (
                        d.find_elements(By.NAME, "user[login]")
                        or d.find_elements(By.NAME, "username")
                        or d.find_elements(By.XPATH, "//div[contains(text(), 'Account Balance:')]")
                    ))
                    return True
                except TimeoutException:
                    return False

            def _es_pagina_no_encontrada(espera):
                """True si, tras resolver el captcha anti-bot, quedamos en
                .../login mostrando el "Page not found" de IDT -el caso
                puntual que dispara el reintento con la URL base-."""
                try:
                    WebDriverWait(driver, espera).until(
                        lambda d: "/login" in (d.current_url or "").lower()
                    )
                except TimeoutException:
                    return False
                try:
                    body_text = driver.find_element(By.TAG_NAME, "body").text
                    return "page not found" in body_text.lower()
                except Exception:
                    return False

            def _manejar_challenge_anti_bot():
                """Se llama justo después de mandar el usuario o la
                contraseña -el challenge de Radware/ShieldSquare
                (validate.perfdrive.com) puede aparecer en cualquiera de
                los dos pasos, no siempre en el mismo-. Si estamos ahí,
                espera a que se resuelva solo (vía JS, sin intervención) y,
                si al salir quedamos en el "Page not found" de .../login,
                recarga con la URL base. Devuelve True si tuvo que recargar
                -la página quedó "desde cero" y quien llama puede necesitar
                reenviar el usuario-."""
                recargo = False
                try:
                    if "validate.perfdrive.com" in (driver.current_url or "").lower():
                        print("IDT - challenge anti-bot detectado (validate.perfdrive.com), "
                              "esperando a que se resuelva solo...", flush=True)
                        try:
                            WebDriverWait(driver, 60).until(
                                lambda d: "validate.perfdrive.com" not in (d.current_url or "").lower()
                            )
                        except TimeoutException:
                            pass

                    if _es_pagina_no_encontrada(espera=3):
                        print("IDT - tras el challenge anti-bot quedó en 'Page not found' "
                              "(.../login); recargando con la URL base...", flush=True)
                        driver.get(TARGET_URL)
                        _pagina_cargo_bien(espera=15)
                        recargo = True
                except Exception:
                    pass
                return recargo

            def _enviar_usuario():
                """Completa y manda el campo de usuario -soporta las dos
                variantes de nombre que usa IDT según la pantalla-."""
                try:
                    user_input = wait.until(EC.presence_of_element_located((By.NAME, "user[login]")))
                except TimeoutException:
                    try:
                        user_input = wait.until(EC.presence_of_element_located((By.NAME, "username")))
                    except Exception:
                        return
                try:
                    user_input.clear()
                    user_input.send_keys(config["usuario"])
                    try:
                        driver.find_element(By.NAME, "commit").click()
                    except Exception:
                        pass
                    time.sleep(0.6)
                except Exception:
                    pass

            if _es_pagina_no_encontrada(espera=3) or not _pagina_cargo_bien(espera=6):
                # El challenge anti-bot de IDT (Radware/ShieldSquare,
                # validate.perfdrive.com) a veces, al resolverlo, redirige a
                # una URL (ej. .../login) que el propio sitio de IDT devuelve
                # como "Page not found" -aunque el check en sí se haya
                # pasado bien-. Volver a pedir la URL raíz (la que siempre
                # funcionó) alcanza para que cargue la página real, ya con
                # las cookies de la verificación puestas.
                print("IDT - la página no trajo login ni saldo (posible 'Page not found' tras el "
                      "check anti-bot); reintentando con la URL base...", flush=True)
                driver.get(TARGET_URL)
                _pagina_cargo_bien(espera=15)

            # --- ¿Ya hay sesión iniciada? ---
            # Con el perfil de Chrome persistente (PERFIL_CHROME_IDT) las
            # cookies sobreviven entre corridas: si la sesión anterior sigue
            # vigente, IDT manda directo a la pantalla de saldo y el
            # formulario de login nunca aparece. Sin este chequeo, cada uno
            # de los wait.until() de login/password de abajo tarda sus 40s
            # completos en agotarse esperando un campo que no existe -varios
            # minutos perdidos solo para terminar leyendo el saldo igual-.
            ya_logueado = False
            try:
                WebDriverWait(driver, 5).until(
                    EC.presence_of_element_located((By.XPATH, "//div[contains(text(), 'Account Balance:')]"))
                )
                ya_logueado = True
            except TimeoutException:
                ya_logueado = False

            if not ya_logueado:
                # --- Login usuario ---
                _enviar_usuario()

                # El challenge anti-bot (validate.perfdrive.com) puede
                # aparecer justo acá, apenas se manda el usuario. Si tocó
                # recargar la URL base, la pantalla vuelve a pedir el
                # usuario desde cero -hay que reenviarlo-.
                if _manejar_challenge_anti_bot():
                    _enviar_usuario()

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
                        _escribir_lento(pwd_input, config["password"], duracion_seg=6.0)
                        try:
                            driver.find_element(By.NAME, "commit").click()
                        except Exception:
                            pwd_input.send_keys(Keys.ENTER)
                        time.sleep(0.8)
                    except Exception:
                        pass

                # El challenge anti-bot también puede aparecer acá, justo
                # después de enviar la contraseña. Si ya estamos logueados
                # (la recarga trajo el saldo directo), lo de más abajo
                # simplemente no encuentra preguntas de seguridad ni pisa
                # nada -es inofensivo llamarlo igual-.
                _manejar_challenge_anti_bot()

            # --- Preguntas de seguridad (solo si tocó loguearse recién) ---
            if not ya_logueado:
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