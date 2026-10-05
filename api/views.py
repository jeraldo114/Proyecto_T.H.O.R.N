"""Respuestas JSON; las operaciones de I/O se esperan mediante await."""

from __future__ import annotations

import asyncio
import json
import secrets
from dataclasses import asdict

from django.conf import settings
from django.core.handlers.asgi import ASGIRequest
from django.http import JsonResponse
from django.views.decorators.http import require_GET, require_POST

from core.event_bus import EventBusClosedError
from core.events import EventType, ThornEvent
from core.runtime import RuntimeState, ThornRuntime

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
        "name": "T.H.O.R.N", "version": "Bastion 8", "ready": ready,
    }, status=200 if ready else 503)


@require_GET
async def status(request: ASGIRequest) -> JsonResponse:
    """Muestra estado y contadores del bus mediante una lectura en memoria."""
    runtime = _runtime(request)
    if runtime is None:
        return JsonResponse({"error": "runtime_unavailable"}, status=503)
    return JsonResponse({
        "name": "T.H.O.R.N", "version": "Bastion 8",
        "runtime": {
            "state": runtime.state, "database_enabled": settings.THORN_USE_DATABASE,
        },
        "events": asdict(runtime.bus.stats),
    })


@require_POST
async def publish_event(request: ASGIRequest) -> JsonResponse:
    """Autentica, valida JSON y encola un evento; 202 significa aceptado."""
    expected_token = settings.THORN_API_TOKEN
    if not expected_token:
        return JsonResponse({"error": "api_not_configured"}, status=503)
    authorization = request.headers.get("Authorization", "")
    supplied_token = authorization.removeprefix("Bearer ")
    if not authorization.startswith("Bearer ") or not secrets.compare_digest(
        supplied_token.encode("utf-8"), expected_token.encode("utf-8"),
    ):
        response = JsonResponse({"error": "unauthorized"}, status=401)
        response["WWW-Authenticate"] = "Bearer"
        return response

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
