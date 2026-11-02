"""Separa recursos del ejecutable y datos privados, tambien en modo compilado."""
from __future__ import annotations

import os
from pathlib import Path
from .version import STORAGE_DIRECTORY

RESOURCE_ROOT = Path(__file__).resolve().parents[1]


def compiled() -> bool:
    """Nuitka define __compiled__ en cada modulo que compila."""
    return '__compiled__' in globals()


def data_root() -> Path:
    """THORN_DATA_DIR es una ruta de configuracion, nunca una credencial."""
    # NOTA DEL OPERADOR: recursos y datos tienen casa distinta en el ejecutable.
    # No reubiques los datos para ordenar la vista: DPAPI depende de esa ruta.
    configured = os.getenv('THORN_DATA_DIR')
    if configured:
        return Path(configured).expanduser().resolve()
    if compiled():
        return (Path(os.environ['LOCALAPPDATA']) / 'THORN' / STORAGE_DIRECTORY).resolve()
    return RESOURCE_ROOT
