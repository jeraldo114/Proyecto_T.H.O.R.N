"""Emision autenticada, renovacion y revocacion; no existe un bootstrap anonimo."""

from __future__ import annotations

import secrets

from django.conf import settings
from django.core.handlers.asgi import ASGIRequest
from django.http import JsonResponse
from django.views.decorators.http import require_POST

from .assistant import body
from .views import _runtime


def credential(request: ASGIRequest) -> str | None:
    values = [value for key, value in request.scope.get("headers", []) if key.lower() == b"authorization"]
    if len(values) != 1 or len(values[0]) > 512 or not values[0].startswith(b"Bearer "):
        return None
    try:
        return values[0][7:].decode("ascii")
    except UnicodeError:
        return None


async def handle(request: ASGIRequest, operation: str) -> JsonResponse:
    token = credential(request)
    runtime = _runtime(request)
    if runtime is None:
        return JsonResponse({"error": "runtime_unavailable"}, status=503)
    is_root = token is not None and bool(settings.THORN_API_TOKEN) and secrets.compare_digest(token, settings.THORN_API_TOKEN)
    if not token or not is_root and runtime.sessions.find(token) is None:
        return JsonResponse({"error": "unauthorized"}, status=401)
    try:
        body(request, set())
    except ValueError:
        return JsonResponse({"error": "invalid_fields"}, status=400)
    try:
        if operation == "exchange" and is_root:
            result = runtime.sessions.issue()
        elif operation in {"exchange", "renew"}:
            result = runtime.sessions.renew(token)
        elif runtime.sessions.revoke(token):
            result = {"revoked": True}
        else:
            raise ValueError("invalid_session")
    except ValueError:
        return JsonResponse({"error": "unauthorized"}, status=401)
    except RuntimeError:
        return JsonResponse({"error": "session_limit"}, status=409)
    response = JsonResponse(result)
    response["Cache-Control"] = "no-store"
    response["Pragma"] = "no-cache"
    return response


@require_POST
async def exchange(request: ASGIRequest) -> JsonResponse:
    return await handle(request, "exchange")


@require_POST
async def renew(request: ASGIRequest) -> JsonResponse:
    return await handle(request, "renew")


@require_POST
async def revoke(request: ASGIRequest) -> JsonResponse:
    return await handle(request, "revoke")
