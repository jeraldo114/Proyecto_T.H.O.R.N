"""Respuestas JSON; las operaciones de I/O se esperan mediante await."""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict
from datetime import datetime, timezone

from django.conf import settings
from django.core.handlers.asgi import ASGIRequest
from django.http import JsonResponse
from django.views.decorators.http import require_GET, require_POST

from core.event_bus import EventBusClosedError
from core.events import EventType, ThornEvent
from core.runtime import RuntimeState, ThornRuntime
from core.sessions import authorize
from core.version import RELEASE

ALLOWED_EVENT_TYPES = {EventType.COMMAND_RECEIVED, EventType.SYSTEM_ALERT}


def _runtime(request: ASGIRequest) -> ThornRuntime | None:
    """Obtiene el runtime inyectado por ASGI; nunca crea un pool por peticion."""
    runtime = request.scope.get("thorn.runtime")
    if isinstance(runtime, ThornRuntime) and runtime.state is RuntimeState.RUNNING:
        return runtime
    return None


@require_GET
async def health(request: ASGIRequest) -> JsonResponse:
    """Indica si termino el arranque del nucleo; no realiza consultas MySQL."""
    ready = _runtime(request) is not None
    return JsonResponse({
        "name": "T.H.O.R.N", "version": RELEASE, "ready": ready,
    }, status=200 if ready else 503)


@require_GET
async def status(request: ASGIRequest) -> JsonResponse:
    """Muestra estado y contadores del bus mediante una lectura en memoria."""
    runtime = _runtime(request)
    if runtime is None:
        return JsonResponse({"error": "runtime_unavailable"}, status=503)
    return JsonResponse({
        "name": "T.H.O.R.N", "version": RELEASE,
        "runtime": {
            "state": runtime.state, "database_enabled": settings.THORN_USE_DATABASE,
            "uptime_seconds": runtime.uptime_seconds,
        },
        "server_time": datetime.now(timezone.utc).isoformat(),
        "capabilities": {
            "system_sensor": runtime.system_sensor is not None,
            "language_model": bool(runtime.services and runtime.services.language.status["available"]),
            "voice": bool(runtime.services and runtime.services.voice.status["available"]),
            "os_actions": bool(runtime.services and runtime.services.actions.enabled),
            "alerts": runtime.services is not None,
            "audit": bool(runtime.services and runtime.services.audit.enabled),
        },
        "events": asdict(runtime.bus.stats),
    })


def _authenticate(request: ASGIRequest) -> JsonResponse | None:
    """Autoridad para administracion o sesiones acotadas; solo hashes en RAM."""
    runtime = request.scope.get("thorn.runtime")
    code = authorize(request.scope.get("headers", []), root=settings.THORN_API_TOKEN,
        authority=runtime.sessions if runtime else None, path=request.path, method=request.method)
    if code is not None:
        response = JsonResponse({"error": {401: "unauthorized", 403: "insufficient_scope", 503: "api_not_configured"}[code]}, status=code)
        if code == 401:
            response["WWW-Authenticate"] = "Bearer"
        return response
    return None


@require_GET
async def activity(request: ASGIRequest) -> JsonResponse:
    """Lee hasta 120 eventos observados en memoria; requiere autenticacion."""
    denied = _authenticate(request)
    if denied is not None:
        return denied
    runtime = _runtime(request)
    if runtime is None:
        return JsonResponse({"error": "runtime_unavailable"}, status=503)
    return JsonResponse({"events": runtime.activity, "capacity": 120, "durable": False})


@require_GET
async def system(request: ASGIRequest) -> JsonResponse:
    """Consulta la muestra cacheada. Nunca recoge telemetria dentro de HTTP."""
    denied = _authenticate(request)
    if denied is not None:
        return denied
    runtime = _runtime(request)
    if runtime is None:
        return JsonResponse({"error": "runtime_unavailable"}, status=503)
    sensor = runtime.system_sensor
    if sensor is None:
        return JsonResponse({"error": "sensor_disabled"}, status=503)
    stats = sensor.stats
    snapshot = sensor.snapshot
    payload: dict[str, object] = {
        "sensor": stats, "snapshot": asdict(snapshot) if snapshot is not None else None,
    }
    if not stats["fresh"]:
        payload["error"] = "sensor_unavailable"
    return JsonResponse(payload, status=200 if stats["fresh"] else 503)


@require_POST
async def publish_event(request: ASGIRequest) -> JsonResponse:
    """Autentica, valida JSON y encola un evento; 202 significa aceptado."""
    denied = _authenticate(request)
    if denied is not None:
        return denied

    runtime = _runtime(request)
    if runtime is None:
        return JsonResponse({"error": "runtime_unavailable"}, status=503)
    if request.content_type != "application/json":
        return JsonResponse({"error": "json_required"}, status=415)
    try:
        data = json.loads(request.body)
    except (ValueError, RecursionError):
        return JsonResponse({"error": "invalid_json"}, status=400)
    if not isinstance(data, dict):
        return JsonResponse({"error": "object_required"}, status=400)
    event_type, payload = data.get("event_type"), data.get("payload", {})
    correlation = data.get("correlation_id")
    if not isinstance(event_type, str) or event_type not in ALLOWED_EVENT_TYPES:
        return JsonResponse({"error": "event_type_not_allowed"}, status=400)
    if not isinstance(payload, dict):
        return JsonResponse({"error": "payload_must_be_object"}, status=400)
    if correlation is not None and (
        not isinstance(correlation, str) or not correlation.strip() or len(correlation) > 128
    ):
        return JsonResponse({"error": "invalid_correlation_id"}, status=400)

    event = ThornEvent(event_type, "api.local", payload, correlation_id=correlation)
    try:
        # La API no espera indefinidamente si otros modulos saturan la cola.
        async with asyncio.timeout(1):
            await runtime.bus.publish(event)
    except (TimeoutError, EventBusClosedError):
        return JsonResponse({"error": "bus_unavailable"}, status=503)
    return JsonResponse({
        "accepted": True, "event_id": event.event_id,
        "correlation_id": event.correlation_id,
    }, status=202)
