"""
Ciclo de consulta de saldos: recorre los proveedores habilitados y
escribe cada saldo en la hoja de Google Sheets configurada.
"""
import time
import logging
from pathlib import Path
from datetime import datetime

from remote_lock import verificar_bloqueo
from .config import save_config
from .chrome_tools import ensure_chrome, ensure_chromedriver, get_robust_driver, cleanup_orphans
from .providers_registry import get_all_providers, sort_providers_by_order

logger = logging.getLogger("main")


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
        for intento in range(1, MAX_INTENTOS + 1):
            try:
                success, msg = provider.get_balance(
                    final_cfg, sh, config['google_sheet_url'],
                    driver_paths={"chrome_exe": chrome_exe, "chromedriver_exe": chromedriver_exe},
                    get_driver_fn=get_robust_driver,
                    headless=not quiere_visible
                )
            except TypeError:
                # Este proveedor todavía no acepta el kwarg 'headless'
                # (p. ej. providers viejos sin actualizar).
                try:
                    success, msg = provider.get_balance(
                        final_cfg, sh, config['google_sheet_url'],
                        driver_paths={"chrome_exe": chrome_exe, "chromedriver_exe": chromedriver_exe},
                        get_driver_fn=get_robust_driver
                    )
                except Exception as e:
                    success, msg = False, str(e)
            except Exception as e:
                success, msg = False, str(e)

            if success:
                break
            if intento < MAX_INTENTOS:
                print(f"  ⚠️  {provider.name} -> intento {intento} falló ({msg}); reintentando...", flush=True)
                time.sleep(2)

        if success:
            print(f"  ✅ {provider.name} -> OK ({msg})", flush=True)
        else:
            print(f"  ❌ {provider.name} -> FALLO tras {MAX_INTENTOS} intento(s): {msg}", flush=True)

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
