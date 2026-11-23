"""
Ciclo de consulta de saldos: recorre los proveedores habilitados y
escribe cada saldo en la hoja de Google Sheets configurada.
"""
import inspect
import time
import logging
from pathlib import Path
from datetime import datetime

from remote_lock import verificar_bloqueo
from .config import save_config
from .chrome_tools import (ensure_chrome, ensure_chromedriver, get_robust_driver, cleanup_orphans,
                           avisar_si_versiones_desparejas)
from .providers_registry import get_all_providers, sort_providers_by_order

logger = logging.getLogger("main")


def _acepta_headless(provider) -> bool:
    """Si get_balance() de este proveedor acepta el kwarg 'headless'.

    Antes esto se averiguaba lanzando la llamada CON headless y atrapando
    TypeError. El problema: un TypeError levantado DENTRO del proveedor
    (una resta contra None, un selector que devolvio algo inesperado) era
    indistinguible de "la firma no acepta headless", asi que se reintentaba
    sin headless -abriendo un navegador visible sin que nadie lo pidiera- y
    el bug real quedaba enmascarado. Mirar la firma responde la pregunta
    exacta sin ejecutar nada.

    Si la firma no se puede inspeccionar (algo envuelto en un decorador sin
    functools.wraps, por ejemplo) se asume que SI lo acepta: es lo que
    cumplen todos los proveedores del proyecto."""
    try:
        parametros = inspect.signature(provider.get_balance).parameters
    except (TypeError, ValueError):
        return True
    if 'headless' in parametros:
        return True
    # **kwargs tambien lo absorbe sin romper.
    return any(p.kind is inspect.Parameter.VAR_KEYWORD for p in parametros.values())


def _resumir_error(msg: str, maximo: int = 300) -> str:
    """Deja solo la parte legible del mensaje de error de un proveedor.

    Las excepciones de Selenium traen pegado el volcado del chromedriver
    ("Stacktrace:" y decenas de lineas "(No symbol) [0x7ff...]"), que no
    aporta nada: el chromedriver que se descarga viene sin simbolos, asi
    que son direcciones de memoria y nada mas. Y cuando la excepcion es un
    TimeoutException sin mensaje propio -lo que lanza wait.until() al
    agotarse si no se le paso un `message=`- el texto queda en un
    "Message:" vacio seguido de esas 20 lineas de hexadecimal: un fallo
    que parece gravisimo y no dice absolutamente nada (visto en IDT,
    2026-11-23). Esto recorta el volcado y, si no sobra nada legible, lo
    dice con palabras."""
    texto = (msg or "").strip()
    corte = texto.find("Stacktrace:")
    if corte != -1:
        texto = texto[:corte]
    # "Message:" es el prefijo que Selenium le pone a todas; sin el texto
    # que deberia seguirlo, no informa de nada.
    texto = " ".join(texto.replace("Message:", " ").split())
    if not texto:
        return "sin detalle (probablemente un timeout esperando un elemento de la pagina)"
    if len(texto) > maximo:
        texto = texto[:maximo].rstrip() + "..."
    return texto


def run_balance_cycle(config: dict):
    try:
        import sys
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

    cred_path = Path(config['credentials_path'])
    if not cred_path.exists():
        print(f"ERROR: Credenciales no encontradas en {cred_path}", flush=True)
        return

    cleanup_orphans()

    if verificar_bloqueo():
        print("\n⚠️  APLICACIÓN BLOQUEADA POR EL ADMINISTRADOR.", flush=True)
        print("   Contacte al soporte para más información.\n", flush=True)
        return

    from oauth2client.service_account import ServiceAccountCredentials
    import gspread

    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_name(str(cred_path), scope)
    client = gspread.authorize(creds)
    sh = client.open_by_url(config['google_sheet_url']).sheet1

    chrome_exe = ensure_chrome()
    chromedriver_exe = ensure_chromedriver()
    aviso_versiones = avisar_si_versiones_desparejas()
    if aviso_versiones:
        print(f"\n  ADVERTENCIA: {aviso_versiones}\n", flush=True)

    providers = get_all_providers()
    providers = sort_providers_by_order(providers, config)

    enabled_names = config.get("enabled_providers", [])
    if not enabled_names:
        enabled_names = [p.name for p in providers]
        config["enabled_providers"] = enabled_names
        save_config(config)

    print("=" * 50, flush=True)
    print("  CHEQUEO DE SALDOS", flush=True)
    print(f"  {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}", flush=True)
    print("=" * 50, flush=True)

    for provider in providers:
        if provider.name not in enabled_names:
            continue
        prov_cfg = config.get("providers_config", {}).get(provider.name, {})
        final_cfg = {}
        for field in provider.config_fields:
            key = field["key"]
            final_cfg[key] = prov_cfg.get(key, field.get("default", ""))

        # --- Aplicar celda personalizada si existe en la configuración ---
        if 'sheet_row' in prov_cfg:
            provider.sheet_row = int(prov_cfg['sheet_row'])
        if 'sheet_col' in prov_cfg:
            provider.sheet_col = int(prov_cfg['sheet_col'])

        print(f"\nProcesando {provider.name}...", flush=True)

        visibles = [str(v).strip() for v in config.get("visible_providers", [])]
        quiere_visible = provider.name in visibles

        MAX_INTENTOS = 2
        success, msg = False, ""
        # Se decide UNA vez, por firma, en vez de deducirlo de un TypeError
        # en tiempo de ejecucion (ver _acepta_headless).
        kwargs_extra = {"headless": not quiere_visible} if _acepta_headless(provider) else {}
        if not kwargs_extra:
            logger.info(f"{provider.name}: get_balance() no acepta 'headless'; se llama sin ese parametro.")
        for intento in range(1, MAX_INTENTOS + 1):
            try:
                success, msg = provider.get_balance(
                    final_cfg, sh, config['google_sheet_url'],
                    driver_paths={"chrome_exe": chrome_exe, "chromedriver_exe": chromedriver_exe},
                    get_driver_fn=get_robust_driver,
                    **kwargs_extra
                )
            except Exception as e:
                # El nombre del tipo adelante: varias excepciones de
                # Selenium llegan con el texto vacio.
                detalle = str(e).strip()
                success, msg = False, f"{type(e).__name__}: {detalle}" if detalle else type(e).__name__

            if success:
                break
            if intento < MAX_INTENTOS:
                print(f"  ⚠️  {provider.name} -> intento {intento} falló "
                      f"({_resumir_error(msg)}); reintentando...", flush=True)
                logger.warning(f"{provider.name}: intento {intento} fallo: {msg}")
                time.sleep(2)

        if success:
            print(f"  ✅ {provider.name} -> OK ({msg})", flush=True)
        else:
            print(f"  ❌ {provider.name} -> FALLO tras {MAX_INTENTOS} intento(s): "
                  f"{_resumir_error(msg)}", flush=True)
            logger.error(f"{provider.name}: fallo tras {MAX_INTENTOS} intento(s): {msg}")

    print("\n" + "=" * 50, flush=True)
    print("  TODAS LAS TAREAS COMPLETADAS", flush=True)
    print("=" * 50, flush=True)

    try:
        sh.update_cell(5, 7, datetime.now().strftime("%H:%M:%S"))
    except Exception:
        pass

    print("\nProceso finalizado. Puede cerrar esta ventana.", flush=True)
    try:
        input()
    except (EOFError, OSError):
        pass
