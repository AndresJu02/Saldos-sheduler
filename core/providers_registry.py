"""
Descubrimiento, orden y configuración de proveedores (los .py en
providers/, más los proveedores genéricos creados desde la GUI).
"""
import sys
import logging
import importlib
import importlib.util
from pathlib import Path
from typing import List

from .paths import BASE_DIR, INTERNAL_DIR
from .config import load_config, save_config

logger = logging.getLogger("main")


def load_providers_from_dir(directory: Path) -> List:
    from base_provider import BaseProvider
    providers = []
    if not directory.exists():
        return providers
    sys.path.insert(0, str(directory.parent))
    for f in directory.glob("*.py"):
        if f.stem == "__init__" or f.stem == "vos_helpers":
            continue
        mod_name = f"providers.{f.stem}"
        spec = importlib.util.spec_from_file_location(mod_name, str(f))
        if spec is None:
            continue
        mod = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(mod)
        except Exception as e:
            logger.warning(f"No se pudo cargar {f.name}: {e}")
            continue
        for attr_name in dir(mod):
            attr = getattr(mod, attr_name)
            if isinstance(attr, type) and issubclass(attr, BaseProvider) and attr != BaseProvider:
                providers.append(attr())
    return providers


def get_all_providers():
    internal = INTERNAL_DIR / 'providers'
    external = BASE_DIR / 'providers'
    providers = load_providers_from_dir(internal)
    nombres = {p.name for p in providers}
    for p in load_providers_from_dir(external):
        if p.name not in nombres:
            providers.append(p)

    # Proveedores genéricos creados desde la GUI (botón "Agregar proveedor")
    try:
        from generic_provider import GenericWebProvider
        cfg = load_config()
        for custom in cfg.get("custom_providers", []):
            nombre = str(custom.get("name", "")).strip()
            if nombre and nombre not in nombres:
                providers.append(GenericWebProvider(custom))
                nombres.add(nombre)
    except Exception as e:
        logger.warning(f"No se pudieron cargar proveedores genéricos: {e}")

    return providers


def get_custom_provider_names(config: dict) -> set:
    """Nombres de proveedores creados con el formulario 'Agregar proveedor'."""
    return {str(c.get("name", "")).strip() for c in config.get("custom_providers", []) if c.get("name")}


import providers.vos_helpers


def sort_providers_by_order(providers: List, config: dict) -> List:
    order = config.get("provider_order", [])
    order = [str(o).strip() for o in order]
    if not order:
        order = [p.name for p in providers]
        config["provider_order"] = order
        save_config(config)
    else:
        existing = set(order)
        for p in providers:
            if p.name not in existing:
                order.append(p.name)
        config["provider_order"] = order
        save_config(config)

    def sort_key(provider):
        try:
            return order.index(provider.name)
        except ValueError:
            return len(order)
    providers.sort(key=sort_key)
    return providers
