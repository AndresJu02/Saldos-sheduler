"""
Proveedor genérico "de pasos": en vez de los 4 campos fijos de
generic_provider.py (usuario/contraseña/enviar/saldo), corre una lista
ORDENADA de pasos de distinto tipo -Ir a URL, Escribir texto, Clic,
Presionar tecla, Esperar, Leer texto- para poder armar flujos de login
más largos (varias pantallas, pasos intermedios) sin necesidad de un
archivo .py a medida. Es lo que arma gui/tabs/proveedores.py con su
editor de pasos reordenable (agregar/quitar/subir/bajar).

Cada proveedor "de pasos" se guarda en scheduler_config.json ->
"custom_providers" con una clave "steps" (lista de dicts); su ausencia es
justo lo que distingue a un proveedor viejo (formato fijo, todavía leído
por GenericWebProvider) de uno nuevo -ver providers_registry.get_all_providers,
que elige la clase según si "steps" está presente-.
"""
import time
import re

from base_provider import BaseProvider
from selenium.webdriver.common.keys import Keys

from generic_provider import BY_MAP, find_in_iframes, DEFAULT_BALANCE_REGEX

TIPOS_PASO = {
    "goto": {"label": "🌐 Ir a URL"},
    "type": {"label": "⌨️  Escribir texto"},
    "click": {"label": "🖱️  Clic en elemento"},
    "key": {"label": "⏎  Presionar tecla"},
    "wait": {"label": "⏳  Esperar"},
    "extract": {"label": "📖  Leer texto"},
}

TECLAS = {
    "ENTER": Keys.ENTER,
    "TAB": Keys.TAB,
    "ESCAPE": Keys.ESCAPE,
}


def describir_paso(paso: dict) -> str:
    """Texto de una línea para mostrar el paso en la lista del editor."""
    tipo = paso.get("type")
    label = TIPOS_PASO.get(tipo, {}).get("label", f"❓ {tipo}")
    if tipo == "goto":
        return f"{label}: {paso.get('url', '')}"
    if tipo == "type":
        return f"{label}: {paso.get('selector', '')}  ←  \"{paso.get('valor', '')}\""
    if tipo == "click":
        return f"{label}: {paso.get('selector', '')}"
    if tipo == "key":
        objetivo = f" en {paso.get('selector')}" if (paso.get("selector") or "").strip() else " (elemento activo)"
        return f"{label}: {paso.get('tecla', 'ENTER')}{objetivo}"
    if tipo == "wait":
        return f"{label}: {paso.get('segundos', 1)}s"
    if tipo == "extract":
        return f"{label}: {paso.get('selector', '')}  →  guarda en \"{paso.get('guardar_como') or 'saldo'}\""
    return label


def migrar_definicion_antigua(cfg: dict) -> list:
    """Convierte un proveedor viejo (campos fijos de generic_provider.py)
    en la lista de pasos equivalente, para poder seguir editándolo en el
    editor nuevo. Se usa solo cuando el proveedor NO tiene ya "steps"."""
    pasos = [{"type": "goto", "url": cfg.get("url", "")}]

    pasos.append({
        "type": "type",
        "selector_type": cfg.get("user_selector_type", "name"),
        "selector": cfg.get("user_selector", ""),
        "valor": "{usuario}",
    })
    pasos.append({
        "type": "type",
        "selector_type": cfg.get("pass_selector_type", "name"),
        "selector": cfg.get("pass_selector", ""),
        "valor": "{password}",
    })

    submit_selector = (cfg.get("submit_selector") or "").strip()
    if submit_selector:
        pasos.append({
            "type": "click",
            "selector_type": cfg.get("submit_selector_type", "css"),
            "selector": submit_selector,
        })
    else:
        pasos.append({"type": "key", "tecla": "ENTER", "selector_type": "", "selector": ""})

    wait_after = cfg.get("wait_after_login")
    try:
        if wait_after not in (None, "") and float(wait_after) > 0:
            pasos.append({"type": "wait", "segundos": wait_after})
    except (TypeError, ValueError):
        pass

    pasos.append({
        "type": "extract",
        "selector_type": cfg.get("balance_selector_type", "xpath"),
        "selector": cfg.get("balance_selector", ""),
        "regex": cfg.get("balance_regex", "") or DEFAULT_BALANCE_REGEX,
        "prefix": cfg.get("prefix", ""),
        "suffix": cfg.get("suffix", ""),
        "guardar_como": "saldo",
    })
    return pasos


def _formatear_valor(valor: str, contexto: dict) -> str:
    """Sustituye {usuario}/{password}/lo que se haya guardado con un paso
    'Leer texto' anterior, al estilo str.format -pero tolerante: si el
    texto no es un placeholder reconocido, se deja tal cual en vez de
    lanzar una excepción-."""
    try:
        return valor.format(**contexto)
    except (KeyError, IndexError, ValueError):
        return valor


def _ejecutar_paso(driver, paso: dict, contexto: dict, timeout: float):
    tipo = paso.get("type")

    if tipo == "goto":
        driver.get(paso.get("url", ""))
        return

    if tipo == "wait":
        time.sleep(float(paso.get("segundos", 1) or 1))
        return

    if tipo == "type":
        by = BY_MAP.get(paso.get("selector_type", "css"), BY_MAP["css"])
        el = find_in_iframes(driver, by, paso.get("selector", ""), timeout=timeout)
        el.clear()
        el.send_keys(_formatear_valor(paso.get("valor", ""), contexto))
        return

    if tipo == "click":
        by = BY_MAP.get(paso.get("selector_type", "css"), BY_MAP["css"])
        el = find_in_iframes(driver, by, paso.get("selector", ""), timeout=timeout, clickable=True)
        el.click()
        return

    if tipo == "key":
        tecla = TECLAS.get(str(paso.get("tecla") or "ENTER").upper(), Keys.ENTER)
        selector = (paso.get("selector") or "").strip()
        if selector:
            by = BY_MAP.get(paso.get("selector_type", "css"), BY_MAP["css"])
            el = find_in_iframes(driver, by, selector, timeout=timeout)
        else:
            el = driver.switch_to.active_element
        el.send_keys(tecla)
        return

    if tipo == "extract":
        by = BY_MAP.get(paso.get("selector_type", "xpath"), BY_MAP["xpath"])
        el = find_in_iframes(driver, by, paso.get("selector", ""), timeout=timeout)
        raw = (el.text or "").strip()
        if not raw:
            raise ValueError("El selector encontró un elemento vacío (revisa que apunte a la celda con el número).")
        pattern = paso.get("regex") or DEFAULT_BALANCE_REGEX
        m = re.search(pattern, raw)
        if not m:
            raise ValueError(f"El texto encontrado fue '{raw}' pero el regex no encontró ningún número dentro.")
        formatted = f"{paso.get('prefix', '')}{m.group()}{paso.get('suffix', '')}".strip()
        contexto[paso.get("guardar_como") or "saldo"] = formatted
        return

    raise ValueError(f"Tipo de paso desconocido: {tipo!r}")


class FlowWebProvider(BaseProvider):
    """
    cfg (definición del sitio) espera:
      name, steps (lista de pasos, ver TIPOS_PASO / _ejecutar_paso),
      usuario_default, password_default, timeout, sheet_row, sheet_col
    """

    def __init__(self, cfg=None):
        cfg = dict(cfg or {})
        self._cfg = cfg
        self.name = str(cfg.get("name") or "Proveedor genérico")
        try:
            self.sheet_row = int(cfg.get("sheet_row", 1))
        except (TypeError, ValueError):
            self.sheet_row = 1
        try:
            self.sheet_col = int(cfg.get("sheet_col", 1))
        except (TypeError, ValueError):
            self.sheet_col = 1

        self.config_fields = [
            {"key": "usuario", "label": "Usuario", "type": "str",
             "default": cfg.get("usuario_default", "")},
            {"key": "password", "label": "Contraseña", "type": "str",
             "default": cfg.get("password_default", "")},
        ]

    def get_balance(self, config, google_sheet, sheet_url, driver_paths, get_driver_fn=None, headless=True):
        cfg = self._cfg
        steps = cfg.get("steps") or []
        if not steps:
            return False, "El proveedor no tiene ningún paso configurado."

        chrome_exe = driver_paths["chrome_exe"]
        chromedriver_exe = driver_paths["chromedriver_exe"]
        driver = get_driver_fn(chrome_exe, chromedriver_exe, headless=headless) if get_driver_fn else None
        if not driver:
            return False, "No se pudo crear el driver"

        timeout = float(cfg.get("timeout", 30) or 30)
        contexto = {
            "usuario": config.get("usuario", ""),
            "password": config.get("password", ""),
        }

        try:
            for i, paso in enumerate(steps, start=1):
                try:
                    _ejecutar_paso(driver, paso, contexto, timeout)
                except Exception as e:
                    return False, f"Paso {i} ({describir_paso(paso)}): {e}"

            resultado = contexto.get("saldo")
            if resultado is None:
                return False, "Ningún paso 'Leer texto' guardó un valor como \"saldo\"."

            if google_sheet is not None:
                google_sheet.update_cell(self.sheet_row, self.sheet_col, resultado)
            return True, resultado
        finally:
            try:
                driver.quit()
            except Exception:
                pass
