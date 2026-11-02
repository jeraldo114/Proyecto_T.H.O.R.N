"""Preparacion de variables y claves locales antes de arrancar Django."""

from __future__ import annotations

import os
import secrets
from pathlib import Path

from dotenv import load_dotenv, set_key
from core.paths import data_root
from core.secure_storage import SECRET_NAMES, StorageUnavailable, load_vault, vault_path

BASE_DIR = data_root()


def protected_environment(root: Path, provided: set[str], *, allow_pending: bool = False) -> bool:
    """Carga secretos sellados; un almacen existente impide volver a texto plano."""
    flag = os.getenv('THORN_SECURE_STORAGE', '0')
    if not allow_pending and (root / 'data/security/migration.pending').exists():
        raise StorageUnavailable()
    if flag not in {'0', '1'}:
        raise StorageUnavailable()
    if flag != '1' and not vault_path(root).exists():
        return False
    vault = load_vault(root)
    for key, value in vault.secrets.items():
        if key not in provided:
            os.environ[key] = value
    os.environ['THORN_SECURE_STORAGE'] = '1'
    return True


def prepare_environment(*, allow_pending: bool = False) -> None:
    """Lee .env y guarda claves aleatorias si aun no existen.

    Esta funcion realiza I/O de archivos. serve.py la ejecuta en un hilo con
    asyncio.to_thread(); los comandos de manage.py la usan antes de un loop.
    Las variables del proceso tienen prioridad sobre las de .env.
    """
    env_path = BASE_DIR / ".env"
    provided_token = bool(os.getenv("THORN_API_TOKEN"))
    provided = set(os.environ)
    load_dotenv(env_path)
    secure = protected_environment(BASE_DIR, provided, allow_pending=allow_pending)
    if os.name == "nt" and not provided_token:
        from .credentials import root_credential
        if secure and not (BASE_DIR / 'data/security/authority.dpapi').is_file():
            raise StorageUnavailable()
        old = os.getenv('THORN_API_TOKEN')
        value = root_credential(BASE_DIR, os.getenv("THORN_API_TOKEN") or None)
        os.environ["THORN_API_TOKEN"] = value
        # Solo tras verificar el almacen; no queda una copia en claro en .env.
        if old:
            set_key(str(env_path), "THORN_API_TOKEN", "")
    for key in ("DJANGO_SECRET_KEY", "THORN_API_TOKEN"):
        if not os.getenv(key):
            if secure:
                raise StorageUnavailable()
            value = secrets.token_urlsafe(48)
            set_key(str(env_path), key, value)
            os.environ[key] = value
