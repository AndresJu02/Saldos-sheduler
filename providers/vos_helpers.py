import time
import re
from selenium.webdriver.common.by import By
# Keys se usa en el camino alternativo del combo "LoginType" de run_site
# (cuando no se puede clickear el desplegable y hay que escribir la opción
# a mano). Faltaba este import, así que ese camino -el de respaldo, el que
# corre justo cuando algo ya salió mal- reventaba con
# "NameError: name 'Keys' is not defined" en vez de hacer el respaldo.
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver import ActionChains
from selenium.common.exceptions import (
    TimeoutException, NoSuchElementException, ElementClickInterceptedException
)

LOGIN_TYPE_VALUE_MAP = {
    "Mapping Gateway": "0",
    "Phone": "1",
}

MAX_CAPTCHA_TRIES = 10

def switch_root(driver):
    try:
        driver.switch_to.default_content()
    except Exception:
        pass

def find_in_iframes(driver, locator, timeout=10, clickable=False):
    switch_root(driver)
    wait = WebDriverWait(driver, timeout)
    cond = EC.element_to_be_clickable(locator) if clickable else EC.presence_of_element_located(locator)
    try:
        el = wait.until(cond)
        return el, None
    except TimeoutException:
        pass

    iframes = driver.find_elements(By.TAG_NAME, "iframe")
    for i, fr in enumerate(iframes):
        switch_root(driver)
        try:
            driver.switch_to.frame(fr)
        except Exception:
            continue
        try:
            el = WebDriverWait(driver, max(4, timeout // 2)).until(cond)
            return el, i
        except TimeoutException:
            nested = driver.find_elements(By.TAG_NAME, "iframe")
            for fr2 in nested:
                try:
                    switch_root(driver)
                    driver.switch_to.frame(fr)
                    driver.switch_to.frame(fr2)
                    el2 = WebDriverWait(driver, 4).until(cond)
                    return el2, i
                except TimeoutException:
                    continue
            continue
    raise TimeoutException(f"No se encontró: {locator}")

def first_of(driver, locators, timeout_each=8, clickable=False):
    last = None
    for loc in locators:
        try:
            el, idx = find_in_iframes(driver, loc, timeout_each, clickable)
            return el
        except Exception as e:
            last = e
    raise TimeoutException(f"No se halló elemento. Último error: {last}")

def parse_balance(text: str) -> str:
    text = text.replace("\n", " ").replace("\r", " ")
    m = re.search(r"Account\s*Balance[\s\u3000:]*([0-9][0-9\.,]*)", text, flags=re.IGNORECASE)
    if not m:
        nums = re.findall(r"[0-9][0-9\.,]+", text)
        if not nums:
            raise ValueError(f"No pude extraer el saldo de: {text!r}")
        raw = nums[-1]
    else:
        raw = m.group(1)
    clean = raw.replace(",", "").strip()
    return clean

def format_cop_no_decimals(num_str: str) -> str:
    try:
        value_int = int(float(num_str))
    except ValueError:
        value_int = int(re.sub(r"\D", "", num_str))
    miles = f"{value_int:,}".replace(",", ".")
    return f"$ {miles} COP"

MASK_LOCATOR = (By.CSS_SELECTOR, "div.ext-el-mask")

def wait_mask_clear(driver, timeout=6):
    try:
        WebDriverWait(driver, timeout).until(EC.invisibility_of_element_located(MASK_LOCATOR))
    except Exception:
        try:
            driver.execute_script("""
                var m=document.querySelector('div.ext-el-mask');
                if(m){ m.style.display='none'; m.style.visibility='hidden'; m.style.zIndex='-1'; }
            """)
        except Exception:
            pass

ERROR_OK_LOCATORS = [
    (By.ID, "ext-gen15"),
    (By.XPATH, "//button[contains(@class,'x-btn-text') and normalize-space(text())='OK']"),
    (By.XPATH, "//button[normalize-space(text())='OK']"),
]

def close_error_popup_if_present(driver, timeout=3) -> bool:
    end = time.time() + timeout
    clicked = False
    while time.time() < end:
        for loc in ERROR_OK_LOCATORS:
            try:
                ok_btn, _ = find_in_iframes(driver, loc, timeout=1, clickable=True)
                try:
                    ok_btn.click()
                except ElementClickInterceptedException:
                    driver.execute_script("arguments[0].click();", ok_btn)
                clicked = True
                break
            except Exception:
                pass
        if clicked:
            break
        time.sleep(0.2)
    if clicked:
        wait_mask_clear(driver, timeout=5)
    return clicked

TRY_ANOTHER_LOCATORS = [
    (By.ID, "ext-gen144"),
    (By.XPATH, "//a[contains(@href,'changeImg') or contains(.,'Try another')]"),
    (By.LINK_TEXT, "Try another"),
    (By.PARTIAL_LINK_TEXT, "Try another"),
]

def refresh_captcha(driver):
    close_error_popup_if_present(driver, timeout=2)
    wait_mask_clear(driver, timeout=5)
    for loc in TRY_ANOTHER_LOCATORS:
        try:
            el, _ = find_in_iframes(driver, loc, timeout=2, clickable=True)
            try:
                el.click()
                time.sleep(0.6)
                return
            except ElementClickInterceptedException:
                driver.execute_script("arguments[0].click();", el)
                time.sleep(0.6)
                return
        except Exception:
            continue
    try:
        driver.execute_script("try{changeImg();}catch(e){}")
        time.sleep(0.6)
    except Exception:
        pass

LOGINTYPE_VISIBLE_LOCATORS = [
    (By.XPATH, "//*[normalize-space(text())='LoginType']/following::input[1]"),
    (By.CSS_SELECTOR, "input[type='text']"),
]
LOGINTYPE_TRIGGER_LOCATORS = [
    (By.XPATH, "//*[normalize-space(text())='LoginType']/following::*[contains(@class,'x-form-trigger')][1]"),
    (By.CSS_SELECTOR, ".x-form-field-wrap .x-form-trigger"),
]

def LOGINTYPE_OPTION_LOCATOR(text):
    return (
        By.XPATH,
        f"//div[contains(@class,'x-combo-list') and contains(@style,'visible')]"
        f"//*[contains(@class,'x-combo-list-item')][normalize-space(text())='{text}']",
    )

NUMBER_LOCATORS = [
    (By.XPATH, "//*[normalize-space(text())='Number']/following::input[1]"),
    (By.NAME, "Number"),
    (By.NAME, "Name"),
    (By.NAME, "terminalName"),
    (By.CSS_SELECTOR, "input[type='text'][name*='number' i]"),
]

PASSWORD_LOCATORS = [
    (By.XPATH, "//*[normalize-space(text())='Password']/following::input[@type='password'][1]"),
    (By.NAME, "Password"),
    (By.NAME, "password"),
    (By.CSS_SELECTOR, "input[type='password']"),
]

CAPTCHA_FIELD_LOCATORS = [
    (By.XPATH, "//*[contains(normalize-space(text()),'Verification')]/following::input[1]"),
    (By.NAME, "checkCode"),
    (By.NAME, "verifyCode"),
    (By.NAME, "randomCode"),
    (By.NAME, "VerificationCode"),
    (By.NAME, "code"),
    (By.CSS_SELECTOR, "input[type='text'][name*='code' i], input[name*='verify' i]"),
]

LOGIN_BUTTON_LOCATORS = [
    (By.ID, "ext-gen123"),
    (By.XPATH, "//button[@id='ext-gen123']"),
    (By.XPATH, "//button[contains(@class,'x-btn-text') and normalize-space(text())='Log in']"),
    (By.XPATH, "//input[@value='Log in' or @value='Login' or @type='submit']"),
    (By.NAME, "button"),
]

BALANCE_LOCATORS = [
    (By.ID, "info5"),
    (By.XPATH, "//li[@id='info5']"),
    (By.XPATH, "//li[contains(normalize-space(.),'Account Balance')]"),
]

# --------------------------------------------------------------------------
# Manejo de la ventana del navegador
#
# Estas funciones existen porque 1980 y SipMovil necesitan que el usuario
# escriba a mano el código de verificación: la ventana se mantiene
# minimizada mientras el robot llena el formulario y se trae al frente
# recién cuando hace falta que una persona escriba.
#
# El windowId NO se guarda en una variable de módulo. Antes sí: se
# cacheaba con el PRIMER navegador del proceso y nunca se limpiaba. Como
# 1980 y SipMovil comparten este archivo y cada uno abre su propio Chrome
# (ver el `driver.quit()` de cada proveedor), el SEGUNDO proveedor de la
# corrida terminaba mandando los comandos CDP al windowId del navegador
# del primero -ya cerrado-. Esas llamadas fallaban en silencio, se saltaba
# el Page.bringToFront y el respaldo (driver.maximize_window) cambia el
# tamaño pero NO trae la ventana al frente: la ventana del captcha quedaba
# enterrada detrás de la consola y había que buscarla y abrirla a mano.
# De ahí el síntoma que se reportó (2026-11-23): "el primero de la
# secuencia se abre solo, el segundo no", sin importar cuál fuera cada
# uno. La consulta del windowId es local y barata, así que se hace cada
# vez y no se comparte nada entre navegadores.
# --------------------------------------------------------------------------

def _get_window_id(driver):
    """windowId del navegador que se le pasa, o None si no se pudo leer."""
    try:
        info = driver.execute_cdp_cmd("Browser.getWindowForTarget", {})
        return info.get("windowId")
    except Exception:
        return None

def _set_window_state(driver, estado):
    """Aplica un windowState de CDP ("minimized", "normal", "maximized").
    Devuelve True si el comando se aceptó."""
    wid = _get_window_id(driver)
    if wid is None:
        return False
    try:
        driver.execute_cdp_cmd(
            "Browser.setWindowBounds", {"windowId": wid, "bounds": {"windowState": estado}}
        )
        return True
    except Exception:
        return False

def minimize_window(driver):
    _set_window_state(driver, "minimized")

def show_window(driver):
    """Trae la ventana al frente para que el usuario pueda escribir el
    código de verificación. Hace todos los intentos disponibles por
    separado -no en un solo try- para que el fallo de uno no se salte los
    demás, que es lo que dejaba la ventana escondida."""
    # Restaurar antes de maximizar: Chrome ignora un "maximized" aplicado
    # directamente sobre una ventana minimizada.
    restaurada = _set_window_state(driver, "normal")
    maximizada = _set_window_state(driver, "maximized")
    if not (restaurada or maximizada):
        # Sin CDP disponible, al menos el tamaño (no da foco, pero es
        # mejor que nada).
        try:
            driver.maximize_window()
        except Exception:
            pass
    # Esto es lo que realmente pone la ventana adelante, y por eso va
    # aparte: antes vivía en el mismo try que setWindowBounds y se lo
    # saltaba cada vez que ese comando fallaba.
    try:
        driver.execute_cdp_cmd("Page.bringToFront", {})
    except Exception:
        pass
    try:
        driver.switch_to.window(driver.current_window_handle)
    except Exception:
        pass


# Cuánto se le da al usuario para escribir el código, y cuánto se espera
# después -ya enviado- a que el panel termine de cargar y aparezca el
# saldo. Son dos plazos distintos a propósito: ver el bucle de vigilancia
# en run_site().
ESPERA_USUARIO_SEG = 120
ESPERA_PANEL_SEG = 60


def _buscar_saldo(driver, timeout_each=1):
    """Devuelve el elemento del saldo si ya está en pantalla, o None."""
    for loc in BALANCE_LOCATORS:
        try:
            el, _ = find_in_iframes(driver, loc, timeout=timeout_each, clickable=False)
            return el
        except Exception:
            continue
    return None


def _guardar_saldo(driver, saldo_el, sheet, sheet_row):
    """Lee el saldo del elemento, lo formatea y lo escribe en la hoja."""
    saldo_texto = driver.execute_script("return arguments[0].textContent;", saldo_el) or saldo_el.text
    saldo_texto = (saldo_texto or "").strip()
    saldo_num = parse_balance(saldo_texto)
    saldo_fmt = format_cop_no_decimals(saldo_num)
    if sheet is not None:
        sheet.update_cell(sheet_row, 4, saldo_fmt)
    print(f"Saldo guardado en D{sheet_row}: {saldo_fmt}", flush=True)


def _buscar_campo_captcha(driver, timeout_each=8):
    """Como first_of() sobre CAPTCHA_FIELD_LOCATORS, pero además devuelve
    con qué locator y en qué iframe se encontró el campo, para poder
    volver a mirarlo después sin ambigüedad (ver _estado_campo_captcha).
    Devuelve (elemento, locator, indice_de_iframe)."""
    last = None
    for loc in CAPTCHA_FIELD_LOCATORS:
        try:
            el, idx = find_in_iframes(driver, loc, timeout_each, clickable=True)
            return el, loc, idx
        except Exception as e:
            last = e
    raise TimeoutException(f"No se halló el campo del código. Último error: {last}")


def _estado_campo_captcha(driver, loc, frame_idx):
    """(sigue_presente, texto_escrito) del campo del código.

    El campo se vuelve a buscar en el mismo contexto donde se encontró, en
    vez de preguntarle al WebElement que se guardó. Es a propósito: las
    otras búsquedas de este archivo (el saldo, el popup de error) cambian
    de iframe por el camino, y un WebElement consultado desde otro frame
    lanza excepción aunque siga perfectamente vivo -lo que haría creer que
    el código ya se envió cuando el usuario todavía no escribió nada-."""
    switch_root(driver)
    try:
        if frame_idx is not None:
            frames = driver.find_elements(By.TAG_NAME, "iframe")
            if frame_idx >= len(frames):
                return False, ""
            driver.switch_to.frame(frames[frame_idx])
        elementos = driver.find_elements(*loc)
        if not elementos:
            return False, ""
        try:
            return True, (elementos[0].get_attribute("value") or "").strip()
        except Exception:
            return True, ""
    except Exception:
        return False, ""
    finally:
        switch_root(driver)


def run_site(driver, site_cfg, sheet, sheet_row):
    """Función adaptada para actualizar la hoja de cálculo."""
    url = site_cfg["url"]
    login_type_text = site_cfg["login_type_text"]
    number = site_cfg["number"]
    password = site_cfg["password"]

    driver.get(url)

    # Comportamiento especial 1980/SIPMOVIL: mostrar/ocultar ventana.
    # Ya no hay ninguna variable de módulo que recuerde si la ventana está
    # a la vista: antes era la global WINDOW_SHOWN, que quedaba en True del
    # proveedor anterior y hacía que el bucle del captcha pudiera no llamar
    # nunca a show_window(). Ahora el bucle la muestra siempre.
    try:
        show_window(driver)
        time.sleep(1.0)
        minimize_window(driver)
    except Exception:
        pass

    # LoginType
    lt_input = first_of(driver, LOGINTYPE_VISIBLE_LOCATORS, clickable=True)
    opened = False
    try:
        trigger = first_of(driver, LOGINTYPE_TRIGGER_LOCATORS, clickable=True)
        trigger.click()
        opened = True
    except Exception:
        pass
    if not opened:
        ActionChains(driver).move_to_element(lt_input).double_click(lt_input).perform()
        lt_input.send_keys(Keys.CONTROL, "a")
        lt_input.send_keys(login_type_text)
        lt_input.send_keys(Keys.ENTER)
    try:
        opt = first_of(driver, [LOGINTYPE_OPTION_LOCATOR(login_type_text)], clickable=True)
        opt.click()
    except Exception:
        pass
    try:
        hidden = first_of(driver, [(By.ID, "loginType")], clickable=False)
        hidden_value = LOGIN_TYPE_VALUE_MAP.get(login_type_text)
        if hidden_value is not None:
            driver.execute_script(
                "arguments[0].value = arguments[1]; "
                "arguments[0].dispatchEvent(new Event('change',{bubbles:true}));",
                hidden, hidden_value
            )
    except Exception:
        pass

    # Number
    number_input = first_of(driver, NUMBER_LOCATORS, clickable=True)
    try:
        number_input.clear()
    except Exception:
        pass
    number_input.send_keys(number)

    # Password
    pwd_input = first_of(driver, PASSWORD_LOCATORS, clickable=True)
    try:
        pwd_input.clear()
    except Exception:
        pass
    pwd_input.send_keys(password)

    # Bucle de captcha
    for attempt in range(1, MAX_CAPTCHA_TRIES + 1):
        # Antes de volver a pedir el código: ¿ya estamos adentro?
        #
        # Este chequeo es el que faltaba. Si el usuario alcanzó a enviar el
        # código en el intento anterior pero el panel terminó de cargar
        # DESPUÉS de que se agotara el plazo de vigilancia, el saldo ya
        # está en pantalla y el login fue exitoso. Sin esto, el intento
        # siguiente arrancaba buscando la casilla del código en una página
        # que ya no la tiene, first_of() agotaba todos sus locators y el
        # proveedor terminaba reportando "No se halló elemento ...
        # input[name*='verify']" -un fallo por no encontrar el captcha en
        # una pantalla donde el login YA había funcionado (reportado
        # 2026-11-23)-.
        saldo_el = _buscar_saldo(driver, timeout_each=1)
        if saldo_el is not None:
            _guardar_saldo(driver, saldo_el, sheet, sheet_row)
            return

        refresh_captcha(driver)

        # Se trae la ventana al frente en CADA intento que necesite a una
        # persona, no solo en el primero: si el intento anterior se agotó
        # porque nadie estaba, lo que hace falta justamente es que la
        # ventana vuelva a aparecer cuando se pide el código de nuevo.
        show_window(driver)

        try:
            captcha_input, captcha_loc, captcha_frame = _buscar_campo_captcha(driver)
        except Exception as e:
            # No hay casilla de código. Puede ser que el login se haya
            # completado justo ahora (se reintenta la búsqueda del saldo
            # con más margen) o que la página esté en un estado inesperado.
            saldo_el = _buscar_saldo(driver, timeout_each=3)
            if saldo_el is not None:
                _guardar_saldo(driver, saldo_el, sheet, sheet_row)
                return
            raise RuntimeError(
                "no apareció la casilla del código de verificación ni el saldo en pantalla "
                f"(la página no está en el login ni en el panel): {e}"
            )

        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", captcha_input)
        time.sleep(0.1)
        try:
            captcha_input.click(); time.sleep(0.05); captcha_input.clear()
        except Exception:
            driver.execute_script("arguments[0].value = '';", captcha_input)

        login_btn = first_of(driver, LOGIN_BUTTON_LOCATORS, clickable=True)
        driver.execute_script("""
            (function(inp, btn){
                if (!inp._enterHookBound) {
                    inp.addEventListener('keydown', function(ev){
                        if (ev.key === 'Enter') { btn.click(); }
                    }, {passive:true});
                    inp._enterHookBound = true;
                }
                inp.focus();
            })(arguments[0], arguments[1]);
        """, captcha_input, login_btn)

        print(f"Escriba el código de verificación en la página y presione Enter "
              f"(intento {attempt}/{MAX_CAPTCHA_TRIES}; hay {ESPERA_USUARIO_SEG} segundos).", flush=True)

        # Vigilancia del resultado. El plazo se maneja con una variable
        # (`fin`) que se puede correr hacia adelante, en vez del límite
        # fijo de antes:
        #
        #   - en cuanto se detecta que el código fue enviado, se cambia al
        #     plazo de carga del panel. Antes no: el reloj arrancaba al
        #     imprimir el mensaje y seguía corriendo igual, así que si el
        #     usuario escribía el código cerca del final, el panel
        #     terminaba de cargar cuando ya nadie estaba mirando y el saldo
        #     -que sí estaba en pantalla- no se capturaba;
        #   - si el usuario empieza a escribir, se le reinicia el plazo una
        #     vez, para que volver al PC sobre la hora no signifique tener
        #     diez segundos útiles.
        fin = time.time() + ESPERA_USUARIO_SEG
        success = None
        saldo_el = None
        enviado = False
        plazo_reiniciado = False

        while success is None and time.time() < fin:
            # 1) El saldo manda: si ya está en pantalla, el login salió bien.
            saldo_el = _buscar_saldo(driver, timeout_each=1)
            if saldo_el is not None:
                success = True
                break

            # 2) Popup de "código incorrecto" -> este intento se perdió.
            if close_error_popup_if_present(driver, timeout=0.8):
                print("Código incorrecto. Escríbalo nuevamente.", flush=True)
                success = False
                break

            if not enviado:
                presente, escrito = _estado_campo_captcha(driver, captcha_loc, captcha_frame)

                # 3) ¿Ya se envió el código? El campo desaparece de la
                #    página en cuanto esta cambia al panel.
                if not presente:
                    enviado = True
                    fin = time.time() + ESPERA_PANEL_SEG
                    print("Código recibido; esperando a que cargue el panel...", flush=True)

                # 4) ¿Empezó a escribir? Se le devuelve el plazo completo
                #    (una sola vez).
                elif escrito and not plazo_reiniciado:
                    plazo_reiniciado = True
                    fin = max(fin, time.time() + ESPERA_USUARIO_SEG)

            time.sleep(0.25)

        if success is True and saldo_el is not None:
            _guardar_saldo(driver, saldo_el, sheet, sheet_row)
            return

        # Si el plazo se agotó con el código ya enviado, el login puede
        # estar terminando justo ahora: el chequeo del saldo que abre el
        # intento siguiente lo va a recoger.
        if success is None:
            motivo = ("el panel no cargó a tiempo" if enviado
                      else "nadie escribió el código a tiempo")
            print(f"Sin respuesta en este intento ({motivo}).", flush=True)

    # Último chance antes de darse por vencido: el panel pudo haber
    # cargado durante el último intento.
    saldo_el = _buscar_saldo(driver, timeout_each=3)
    if saldo_el is not None:
        _guardar_saldo(driver, saldo_el, sheet, sheet_row)
        return

    raise RuntimeError(
        f"No fue posible iniciar sesión: se agotaron los {MAX_CAPTCHA_TRIES} intentos de código "
        "de verificación sin que se llegara a la pantalla del saldo."
    )