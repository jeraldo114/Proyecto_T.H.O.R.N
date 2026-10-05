"""Preparacion de variables y claves locales antes de arrancar Django."""

from __future__ import annotations

import os
import secrets
from pathlib import Path

from dotenv import load_dotenv, set_key

BASE_DIR = Path(__file__).resolve().parents[1]


def prepare_environment() -> None:
    """Lee .env y guarda claves aleatorias si aun no existen.

    Esta funcion realiza I/O de archivos. serve.py la ejecuta en un hilo con
    asyncio.to_thread(); los comandos de manage.py la usan antes de un loop.
    Las variables del proceso tienen prioridad sobre las de .env.
    """
    env_path = BASE_DIR / ".env"
    load_dotenv(env_path)
    for key in ("DJANGO_SECRET_KEY", "THORN_API_TOKEN"):
        if not os.getenv(key):
            value = secrets.token_urlsafe(48)
            set_key(str(env_path), key, value)
            os.environ[key] = value
