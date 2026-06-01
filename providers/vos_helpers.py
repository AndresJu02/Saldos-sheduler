import time
import re
from selenium.webdriver.common.by import By
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

# Variables para manejo de ventana
WINDOW_ID = None
WINDOW_SHOWN = False

def _get_window_id(driver):
    global WINDOW_ID
    if WINDOW_ID is None:
        info = driver.execute_cdp_cmd("Browser.getWindowForTarget", {})
        WINDOW_ID = info.get("windowId")
    return WINDOW_ID

def minimize_window(driver):
    wid = _get_window_id(driver)
    try:
        driver.execute_cdp_cmd("Browser.setWindowBounds", {"windowId": wid, "bounds": {"windowState": "minimized"}})
    except Exception:
        pass

def show_window(driver):
    global WINDOW_SHOWN
    wid = _get_window_id(driver)
    try:
        driver.execute_cdp_cmd("Browser.setWindowBounds", {"windowId": wid, "bounds": {"windowState": "maximized"}})
        driver.execute_cdp_cmd("Page.bringToFront", {})
    except Exception:
        try:
            driver.maximize_window()
        except Exception:
            pass
    WINDOW_SHOWN = True


def run_site(driver, site_cfg, sheet, sheet_row):
    """Función adaptada para actualizar la hoja de cálculo."""
    url = site_cfg["url"]
    login_type_text = site_cfg["login_type_text"]
    number = site_cfg["number"]
    password = site_cfg["password"]

    driver.get(url)

    # Comportamiento especial 1980/SIPMOVIL: mostrar/ocultar ventana
    try:
        show_window(driver)
        time.sleep(1.0)
        minimize_window(driver)
        global WINDOW_SHOWN
        WINDOW_SHOWN = False
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
        refresh_captcha(driver)

        if not WINDOW_SHOWN:
            show_window(driver)

        captcha_input = first_of(driver, CAPTCHA_FIELD_LOCATORS, clickable=True)
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

        print(f"Escriba el código de verificación en la página y presione Enter (intento {attempt}/{MAX_CAPTCHA_TRIES}).")

        end = time.time() + 120
        success = None
        saldo_el = None
        while time.time() < end and success is None:
            if close_error_popup_if_present(driver, timeout=0.8):
                print("Código incorrecto. Escríbalo nuevamente.")
                success = False
                break
            for loc in BALANCE_LOCATORS:
                try:
                    saldo_el, _ = find_in_iframes(driver, loc, timeout=1, clickable=False)
                    success = True
                    break
                except Exception:
                    pass
            time.sleep(0.25)

        if success is True and saldo_el is not None:
            saldo_texto = driver.execute_script("return arguments[0].textContent;", saldo_el) or saldo_el.text
            saldo_texto = (saldo_texto or "").strip()
            saldo_num = parse_balance(saldo_texto)
            saldo_fmt = format_cop_no_decimals(saldo_num)
            sheet.update_cell(sheet_row, 4, saldo_fmt)
            print(f"Saldo guardado en D{sheet_row}: {saldo_fmt}")
            return
        else:
            continue

    raise RuntimeError("No fue posible iniciar sesión: excedidos los intentos de captcha.")