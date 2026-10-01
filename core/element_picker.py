"""
"Elegir en la página": lo mismo que hace el picker de elementos de Automa
(o "Inspeccionar elemento" de Chrome) pero controlado desde Selenium, para
no tener que escribir CSS/XPath a mano al armar un proveedor genérico
(gui/tabs/proveedores.py -> "Agregar proveedor").

Se abre un Chrome visible ya existente (el mismo que usa "Probar ahora"),
se inyecta un overlay que resalta el elemento bajo el mouse y, al hacer
clic, se calcula su selector (id / name / css) SIN dejar pasar el clic
real -así no se envía sin querer un formulario a medio llenar ni se
navega por accidente-. El usuario sigue pudiendo interactuar con la
página normalmente en cuanto termina de elegir (el listener se quita
apenas captura un clic).

Se instala tanto en el documento principal como en cada <iframe> de primer
nivel (igual alcance que find_in_iframes en generic_provider.py) porque
muchos paneles de facturación muestran el saldo dentro de uno. A
diferencia de una extensión de navegador, Selenium puede entrar a un
iframe con switch_to.frame() sin que importe si es de otro dominio -no
aplica la política de mismo origen de JavaScript-, así que no hay
limitación extra por eso.
"""
import time

from selenium.webdriver.common.by import By

_RESULT_VAR = "__proveedorPickerResultado"

# El propio nombre de la variable de resultado se pasa como argumento (en
# vez de quedar quemado en el string) para que no haya que duplicarlo acá
# y en _leer_resultado/elegir_elemento.
_PICKER_JS = r"""
(function(mensaje, resultVar) {
    if (window.__proveedorPickerCleanup) { window.__proveedorPickerCleanup(); }

    function cssPath(el) {
        if (el.id) return '#' + CSS.escape(el.id);
        var partes = [];
        while (el && el.nodeType === Node.ELEMENT_NODE && el.tagName !== 'HTML') {
            var sel = el.nodeName.toLowerCase();
            if (el.id) { sel += '#' + CSS.escape(el.id); partes.unshift(sel); break; }
            var hermano = el, n = 1;
            while ((hermano = hermano.previousElementSibling)) {
                if (hermano.nodeName === el.nodeName) n++;
            }
            if (n !== 1) sel += ':nth-of-type(' + n + ')';
            partes.unshift(sel);
            el = el.parentElement;
        }
        return partes.join(' > ');
    }

    function describir(el) {
        var tag = (el.tagName || '').toLowerCase();
        if (el.id) return {tipo: 'id', valor: el.id};
        if (el.getAttribute && el.getAttribute('name') &&
            ['input', 'select', 'textarea', 'button'].indexOf(tag) !== -1) {
            return {tipo: 'name', valor: el.getAttribute('name')};
        }
        return {tipo: 'css', valor: cssPath(el)};
    }

    var overlay = document.createElement('div');
    overlay.textContent = mensaje;
    // pointer-events:none -si no, el banner tapa (y le roba el clic a)
    // cualquier campo que quede justo debajo, como el usuario/contraseña
    // en formularios pegados arriba de la página-.
    overlay.style.cssText = 'position:fixed;top:0;left:0;right:0;background:#89b4fa;'
        + 'color:#1e1e2e;font:bold 14px "Segoe UI",sans-serif;padding:9px 14px;'
        + 'z-index:2147483647;text-align:center;box-shadow:0 2px 10px rgba(0,0,0,.5);'
        + 'pointer-events:none';
    document.documentElement.appendChild(overlay);

    var resalte = document.createElement('div');
    resalte.style.cssText = 'position:fixed;pointer-events:none;z-index:2147483646;'
        + 'border:2px solid #f38ba8;background:rgba(243,139,168,.25);display:none';
    document.documentElement.appendChild(resalte);

    function onMove(e) {
        var r = e.target.getBoundingClientRect();
        resalte.style.left = r.left + 'px';
        resalte.style.top = r.top + 'px';
        resalte.style.width = r.width + 'px';
        resalte.style.height = r.height + 'px';
        resalte.style.display = 'block';
    }

    function onClick(e) {
        e.preventDefault();
        e.stopPropagation();
        window[resultVar] = describir(e.target);
        limpiar();
    }

    function limpiar() {
        document.removeEventListener('mousemove', onMove, true);
        document.removeEventListener('click', onClick, true);
        overlay.remove();
        resalte.remove();
        window.__proveedorPickerCleanup = null;
    }

    document.addEventListener('mousemove', onMove, true);
    document.addEventListener('click', onClick, true);
    window.__proveedorPickerCleanup = limpiar;
})(arguments[0], arguments[1]);
"""

_CANCELAR_JS = "if (window.__proveedorPickerCleanup) { window.__proveedorPickerCleanup(); }"


def _contextos_de_frames(driver):
    """Lista de contextos donde instalar/leer el picker: None (documento
    principal) + cada <iframe> de primer nivel encontrado ahí mismo."""
    driver.switch_to.default_content()
    try:
        iframes = driver.find_elements(By.TAG_NAME, "iframe")
    except Exception:
        iframes = []
    return [None] + iframes


def _para_cada_contexto(driver, fn):
    """Corre `fn(driver)` primero en el documento principal y luego dentro
    de cada iframe de primer nivel, volviendo siempre al documento
    principal antes de cambiar de contexto (si algún frame ya no existe -
    la página navegó mientras tanto-, simplemente se lo salta)."""
    for ctx in _contextos_de_frames(driver):
        driver.switch_to.default_content()
        if ctx is not None:
            try:
                driver.switch_to.frame(ctx)
            except Exception:
                continue
        try:
            fn(driver)
        except Exception:
            pass
    driver.switch_to.default_content()


def elegir_elemento(driver, mensaje, timeout=120, poll=0.3):
    """Instala el picker (documento + iframes de primer nivel), espera a
    que el usuario haga clic en algo dentro de `timeout` segundos, y
    devuelve {"tipo": "id"|"name"|"css", "valor": "..."} -o None si se
    acabó el tiempo sin ningún clic-. `driver` debe estar ya en la página
    donde se quiere elegir (o el usuario haber navegado a mano, ej. tras
    iniciar sesión, antes de llamar de nuevo a esta función)."""
    def instalar(d):
        # El reset y la instalación van juntos, en el mismo paso por cada
        # contexto: cada iframe tiene su PROPIO objeto `window`, así que
        # limpiar el resultado solo en el documento principal no alcanza
        # -un clic viejo capturado dentro de un iframe se quedaría pegado
        # ahí y una llamada futura lo devolvería como si fuera nuevo-.
        d.execute_script(f"window.{_RESULT_VAR} = null;")
        d.execute_script(_PICKER_JS, mensaje, _RESULT_VAR)

    _para_cada_contexto(driver, instalar)

    resultado = {"valor": None}

    def leer(d):
        valor = d.execute_script(f"return window.{_RESULT_VAR};")
        if valor:
            resultado["valor"] = valor

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and resultado["valor"] is None:
        _para_cada_contexto(driver, leer)
        if resultado["valor"] is not None:
            break
        time.sleep(poll)

    # Se acabó el tiempo (o ya se encontró el resultado): en cualquier
    # caso hay que quitar el overlay de los contextos donde no se hizo
    # clic -su propio listener ya se limpió solo en el que sí-.
    _para_cada_contexto(driver, lambda d: d.execute_script(_CANCELAR_JS))

    return resultado["valor"]
