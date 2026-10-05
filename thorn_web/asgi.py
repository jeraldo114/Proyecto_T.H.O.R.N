"""Aplicacion que Uvicorn carga para servir la API local."""

from __future__ import annotations

import logging
import os

from django.conf import settings
from django.core.asgi import get_asgi_application
from django.urls import get_resolver

from core.events import EventType, ThornEvent
from core.runtime import ThornRuntime
from thorn_web.lifespan import ThornASGI

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "thorn_web.settings")
logger = logging.getLogger(__name__)


def create_runtime() -> ThornRuntime:
    """Construye el runtime local y registra el receptor inicial de eventos."""
    runtime = ThornRuntime(use_database=settings.THORN_USE_DATABASE)

    async def record_event(event: ThornEvent) -> None:
        # Aceptar un evento aun no ejecuta acciones sobre Windows.
        logger.info("Evento procesado: tema=%s id=%s", event.event_type, event.event_id)

    for event_type in EventType:
        runtime.bus.subscribe(event_type, record_event)
    return runtime


django_app = get_asgi_application()
# Resuelve los imports de rutas durante el bootstrap en el hilo de apoyo.
get_resolver().url_patterns
application = ThornASGI(
    django_app, create_runtime, max_request_bytes=settings.THORN_MAX_REQUEST_BYTES,
)
