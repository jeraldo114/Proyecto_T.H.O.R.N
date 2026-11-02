"""Django administra HTTP; core/db_core.py administra las consultas MySQL."""

from __future__ import annotations

import math
import os
from core.document_worker import MAX_PDF_BYTES

SECRET_KEY = os.environ["DJANGO_SECRET_KEY"]
DEBUG = False
ALLOWED_HOSTS = ["127.0.0.1", "localhost", "[::1]"]
ROOT_URLCONF = "thorn_web.urls"
ASGI_APPLICATION = "thorn_web.asgi.application"
INSTALLED_APPS = ["api"]

# Se valida el host con middleware async, manteniendo el acceso local.
MIDDLEWARE = ["api.middleware.local_host_middleware"]

# No hay modelos ORM en esta fase. Toda persistencia usa SQL crudo y aiomysql.
DATABASES: dict[str, object] = {}
TEMPLATES: list[dict[str, object]] = []
LOGGING_CONFIG = None  # El supervisor ya instala QueueHandler/QueueListener.
USE_TZ = True
TIME_ZONE = "America/Santiago"
LANGUAGE_CODE = "es"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

THORN_API_TOKEN = os.environ["THORN_API_TOKEN"]
_database_flag = os.getenv("THORN_USE_DATABASE", "1").lower()
if _database_flag not in {"1", "0", "true", "false"}:
    raise ValueError("THORN_USE_DATABASE debe ser 1, 0, true o false.")
THORN_USE_DATABASE = _database_flag in {"1", "true"}
_sensor_flag = os.getenv("THORN_SYSTEM_SENSOR", "1").lower()
if _sensor_flag not in {"1", "0", "true", "false"}:
    raise ValueError("THORN_SYSTEM_SENSOR debe ser 1, 0, true o false.")
THORN_SYSTEM_SENSOR = _sensor_flag in {"1", "true"}
THORN_SENSOR_INTERVAL = float(os.getenv("THORN_SENSOR_INTERVAL", "2"))
if not math.isfinite(THORN_SENSOR_INTERVAL) or THORN_SENSOR_INTERVAL < 0.2:
    raise ValueError("THORN_SENSOR_INTERVAL debe ser finito y >= 0.2 s.")
THORN_MAX_REQUEST_BYTES = 16 * 1024
# NOTA DEL OPERADOR: ASGI ya limita JSON a 16 KiB y autentica PDF/PCM antes
# de recibirlos. Django no debe volcar esos buffers validados al disco del loop.
DATA_UPLOAD_MAX_MEMORY_SIZE = MAX_PDF_BYTES
FILE_UPLOAD_MAX_MEMORY_SIZE = MAX_PDF_BYTES + 1
