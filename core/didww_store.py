"""
Cliente de la API v3 de DIDWW para COMPRAR líneas (Tienda DID).

Porta a Python la lógica de la app "TIENDA DID" (WinForms/C#,
github.com/AndresJu02/TIENDA-DID: Services/DidwwApi.cs + DidwwClient.cs)
para ofrecerla como una pestaña más de esta aplicación, en vez de un
segundo ejecutable aparte con su propio runtime (.NET).

A diferencia del original, la API Key de DIDWW NUNCA se carga desde un
archivo público de GitHub (así estaba el original: cualquiera con el link
del repo podía leerla) — aquí siempre se recibe como parámetro y quien
llama (gui/tabs/tienda_did_tab.py) la toma de la misma configuración ya
usada por el resto de la app (Proveedores -> DIDWW -> Configurar /
providers_config.DIDWW.api_key), igual que hace didww_renovacion.py.

Este módulo es solo el cliente HTTP (sin estado, sin GUI): cada función
recibe la api_key explícitamente y devuelve listas/dicts ya parseados del
JSON:API de DIDWW (la clave "data" de la respuesta).
"""
import requests

DIDWW_API_BASE_URL = "https://api.didww.com/v3"


class DidwwApiKeyError(Exception):
    """DIDWW rechazó la API Key (401/403). Se separa de cualquier otro
    error HTTP para que la GUI pueda mostrar un aviso claro y específico
    en vez del mensaje genérico de `requests` ("401 Client Error: ...")."""
    pass


def _headers(api_key):
    return {
        "Accept": "application/vnd.api+json",
        "Content-Type": "application/vnd.api+json",
        "Api-Key": api_key,
    }


def _check_response(resp):
    if resp.status_code in (401, 403):
        raise DidwwApiKeyError(
            "DIDWW rechazó la API Key (HTTP {}). Verifica que esté bien copiada en "
            "Configuración o en Proveedores → DIDWW → Configurar.".format(resp.status_code)
        )
    resp.raise_for_status()


def _get(api_key, endpoint):
    resp = requests.get(f"{DIDWW_API_BASE_URL}/{endpoint}", headers=_headers(api_key), timeout=20)
    _check_response(resp)
    return resp.json()


def _post(api_key, endpoint, body):
    resp = requests.post(f"{DIDWW_API_BASE_URL}/{endpoint}", headers=_headers(api_key), json=body, timeout=20)
    _check_response(resp)
    return resp.json()


def get_balance(api_key):
    """Saldo actual de la cuenta DIDWW (GET /balance). Devuelve el atributo
    "balance" (saldo disponible, sin contar línea de crédito) como float."""
    attrs = (_get(api_key, "balance").get("data") or {}).get("attributes") or {}
    return float(attrs.get("balance") or 0)


def get_countries(api_key):
    return _get(api_key, "countries").get("data", [])


def get_cities(api_key, country_id):
    return _get(api_key, f"cities?filter[country.id]={country_id}").get("data", [])


def get_regions(api_key, country_id):
    return _get(api_key, f"regions?filter[country.id]={country_id}").get("data", [])


def get_did_group_types(api_key):
    return _get(api_key, "did_group_types").get("data", [])


def get_did_groups(api_key, country_id, city_id=None, region_id=None, type_id=None):
    """Grupos DID (prefijos) disponibles para un país, filtrando
    opcionalmente por ciudad O región (no ambas a la vez) y por tipo
    (fijo/móvil/etc). Unifica los distintos métodos que tenía el C#
    original (GetDidGroupsPrefijos / GetDidGroups_ByRegion / _ByCity /
    _Mobile), que en el fondo armaban la misma consulta con distintos
    filtros combinados."""
    endpoint = (
        "did_groups?include=stock_keeping_units&filter[is_available]=true"
        f"&filter[country.id]={country_id}"
    )
    if city_id:
        endpoint += f"&filter[city.id]={city_id}"
    if region_id:
        endpoint += f"&filter[region.id]={region_id}"
    if type_id:
        endpoint += f"&filter[did_group_type.id]={type_id}"
    return _get(api_key, endpoint).get("data", [])


def get_available_dids(api_key, did_group_id):
    endpoint = f"available_dids?filter[did_group.id]={did_group_id}&include=did_group"
    return _get(api_key, endpoint).get("data", [])


def get_valid_sku_id(api_key, did_group_id, channels=2):
    """Busca, entre los SKU del grupo DID, el que tiene exactamente
    `channels` canales incluidos (2 por defecto, igual que el C# original
    -GetValidSkuId-, que solo aceptaba SKUs de 2 canales). Devuelve None
    si no hay ninguno con ese valor exacto."""
    endpoint = f"did_groups/{did_group_id}/stock_keeping_units"
    skus = _get(api_key, endpoint).get("data", [])
    for sku in skus:
        if str((sku.get("attributes") or {}).get("channels_included_count")) == str(channels):
            return sku["id"]
    return None


def create_order(api_key, available_did_id, sku_id):
    """Compra UN número puntual (el que el usuario eligió de la lista de
    disponibles). Acción real, con costo en la cuenta de DIDWW."""
    body = {
        "data": {
            "type": "orders",
            "attributes": {
                "items": [
                    {
                        "type": "did_order_items",
                        "attributes": {
                            "available_did_id": available_did_id,
                            "sku_id": sku_id,
                        },
                    }
                ]
            },
        }
    }
    return _post(api_key, "orders", body)


def create_order_random(api_key, sku_id, qty, allow_back_ordering=True):
    """Compra `qty` números al azar dentro de un grupo/prefijo (sin elegir
    el número exacto). Acción real, con costo en la cuenta de DIDWW."""
    body = {
        "data": {
            "type": "orders",
            "attributes": {
                "allow_back_ordering": allow_back_ordering,
                "items": [
                    {
                        "type": "did_order_items",
                        "attributes": {
                            "qty": qty,
                            "sku_id": sku_id,
                        },
                    }
                ]
            },
        }
    }
    return _post(api_key, "orders", body)
