from abc import ABC, abstractmethod
from typing import Dict, List, Tuple

class BaseProvider(ABC):
    # Nombre que aparecerá en la interfaz
    name: str = "Proveedor Genérico"
    
    # Campos de configuración que puede editar el usuario
    # Formato: [{"key": "usuario", "label": "Usuario", "type": "str", "default": ""}, ...]
    config_fields: List[Dict] = []
    
    # Fila y columna en la hoja de cálculo donde se guardará el saldo (1-indexado)
    sheet_row: int = 1
    sheet_col: int = 1

    @abstractmethod
    def get_balance(self, config: dict, google_sheet, sheet_url: str) -> Tuple[bool, str]:
        """
        Ejecuta la extracción del saldo.
        Recibe:
          - config: diccionario con los valores de config_fields.
          - google_sheet: objeto sheet de gspread (puede ser None si no está disponible).
        Retorna: (éxito: bool, mensaje o saldo formateado).
        Si tiene éxito, debe actualizar la celda correspondiente en la hoja.
        """
        pass