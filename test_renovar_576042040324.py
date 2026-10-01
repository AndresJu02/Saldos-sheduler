#!/usr/bin/env python3
"""
Prueba puntual sobre UNA sola línea: 576042040324 (id cfda56e9-0e31-4f98-
8c19-c7f6c27d8528).

Motivo: en el último intento de renovación real, esta línea quedó con
billing_cycles_count=1 pero expires_at NO se movió (a diferencia de la
576042040696, que sí avanzó). Como renovar_did() en didww_renovacion.py
manda billing_cycles_count=1 para líneas activas, si el valor YA estaba en 1
no hay ningún cambio de estado que dispare el "ciclo nuevo" -> no-op.

Esta prueba fuerza el reset 1 -> 0 antes de pedir el renew, para que el
PATCH de renovación sí represente un salto de estado (0 -> 1):
  - Si billing_cycles_count actual == 1: se manda un PATCH forzando 0.
  - Si ya está en 0: no se toca ese campo antes del renew.
  - Cualquier otro valor: se dejan ambos PATCH tal cual, sin decidir nada
    especial (no debería pasar en la práctica).

Reutiliza las funciones ya existentes de didww_renovacion.py (misma API Key,
mismo helper de headers) para no duplicar lógica. NO modifica ese archivo.
Si el resultado es correcto (expires_at avanza), esta lógica se traslada a
renovar_did().

ADVERTENCIA: esto hace PATCH reales contra la API de DIDWW (no hay dry-run
acá) y la renovación tiene costo real ($9.70 según la interfaz de DIDWW).
Ejecutar solo cuando se quiera probar de verdad.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import didww_renovacion as dw

NUMERO = "576042040324"


def _patch(api_key, did_id, attributes):
    body = {"data": {"id": did_id, "type": "dids", "attributes": attributes}}
    resp = dw.requests.patch(
        f"{dw.DIDWW_API_BASE_URL}/dids/{did_id}",
        headers=dw._didww_headers(api_key),
        json=body,
        timeout=20,
    )
    resp.raise_for_status()
    return resp.json()


def main():
    cfg = dw.load_didww_renovacion_config()
    api_key = cfg.get("didww_api_key")
    if not api_key:
        print("ERROR: no hay API Key de DIDWW configurada (Proveedores -> DIDWW -> Configurar).")
        return

    did = dw.buscar_did_por_numero(api_key, NUMERO)
    if not did:
        print(f"No se encontró ningún DID con el número {NUMERO}.")
        return

    did_id = did["id"]
    attrs = did.get("attributes") or {}
    billing_cycles = attrs.get("billing_cycles_count")
    expires_antes = attrs.get("expires_at")
    print(f"DID {did_id} ({NUMERO}): billing_cycles_count actual={billing_cycles}, expires_at actual={expires_antes}")

    if billing_cycles == 1:
        print("billing_cycles_count está en 1 -> se fuerza a 0 antes de renovar.")
        resultado_reset = _patch(api_key, did_id, {"billing_cycles_count": 0})
        print("Respuesta reset a 0:", json.dumps(resultado_reset, ensure_ascii=False))
    elif billing_cycles == 0:
        print("billing_cycles_count ya está en 0 -> no se toca antes de renovar.")
    else:
        print(f"billing_cycles_count tiene un valor inesperado ({billing_cycles!r}), se continúa sin tocarlo.")

    print("Enviando renew (billing_cycles_count=1)...")
    resultado = _patch(api_key, did_id, {"billing_cycles_count": 1})
    expires_despues = (resultado.get("data", {}).get("attributes", {}) or {}).get("expires_at")
    print("Respuesta renew:", json.dumps(resultado, ensure_ascii=False))
    print(f"expires_at: antes={expires_antes} -> después={expires_despues}")

    if expires_despues and expires_despues != expires_antes:
        print("OK: expires_at avanzó. La lógica funcionó para esta línea.")
    else:
        print("FALLO: expires_at no cambió. Hace falta seguir investigando.")


if __name__ == "__main__":
    main()
