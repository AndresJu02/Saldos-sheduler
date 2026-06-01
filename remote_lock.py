import requests
import logging

logger = logging.getLogger("main")

# ⚠️ CAMBIA esta URL por la de tu archivo bloqueo.json en GitHub
BLOQUEO_URL = "https://raw.githubusercontent.com/tu_usuario/tu_repo/main/bloqueo.json"

def verificar_bloqueo() -> bool:
    """
    Consulta el archivo de bloqueo en GitHub.
    Retorna True si está bloqueado (estado=1), False si está permitido (estado=0).
    En caso de error de conexión, permite la ejecución (False) para no depender de internet.
    """
    try:
        resp = requests.get(BLOQUEO_URL, timeout=5)
        resp.raise_for_status()
        data = resp.json()
        estado = data.get("estado", 0)
        if estado == 1:
            return True   # Bloqueado
        else:
            return False  # Permitido
    except Exception as e:
        # Si no hay internet o falla la petición, permitir (no bloquear por error)
        logger.warning(f"No se pudo verificar bloqueo remoto: {e}. Se permite la ejecución.")
        return False