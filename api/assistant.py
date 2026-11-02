"""API privada del asistente. Ningun endpoint acepta shell, rutas o ejecutables."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from functools import wraps
from uuid import uuid4

from django.core.handlers.asgi import ASGIRequest
from django.http import HttpResponse, JsonResponse
from django.views.decorators.http import require_GET, require_POST

from core.services import AssistantServices
from core.version import RELEASE, MODEL_PROFILE
from .views import _authenticate, _runtime

import json

logger = logging.getLogger(__name__)
Handler = Callable[..., Awaitable[HttpResponse]]


def private(handler: Handler) -> Callable[[ASGIRequest], Awaitable[HttpResponse]]:
    """Autentica antes de acceder a datos, con errores publicos acotados."""
    @wraps(handler)
    async def wrapped(request: ASGIRequest, *args: object, **kwargs: object) -> HttpResponse:
        denied = _authenticate(request)
        if denied is not None:
            return denied
        runtime = _runtime(request)
        if runtime is None or runtime.services is None:
            return JsonResponse({"error": "assistant_unavailable"}, status=503)
        try:
            return await handler(request, runtime.services, *args, **kwargs)
        except ValueError as exc:
            return JsonResponse({"error": str(exc)}, status=400)
        except RuntimeError as exc:
            allowed = {"actions_require_windows_and_database", "too_many_pending_actions",
                       "action_busy", "audit_required", "action_failed", "action_outcome_unrecorded", "database_required",
                       "model_busy", "engine_unavailable", "model_missing", "unchecked", "model_query_failed",
                       "voice_busy", "voice_disabled", "voice_missing_key", "voice_missing_voice",
                       "voice_invalid_configuration", "voice_auth_failed", "voice_not_found",
                       "voice_invalid_audio", "voice_rate_limited", "voice_provider_unavailable", "voice_closed"}
            allowed.update({"voice_model_missing", "voice_engine_missing", "voice_model_invalid",
                            "voice_loading", "voice_synthesis_failed", "voice_policy_blocked", "web_paid_provider_blocked"})
            allowed.add('secure_storage_unavailable')
            allowed.add('conversation_memory_unavailable')
            allowed.update({"interaction_busy", "interaction_cancelled", "interaction_capacity", "document_busy"})
            allowed.update({"microphone_busy", "microphone_disabled", "microphone_closed",
                            "microphone_model_missing", "microphone_engine_missing", "microphone_loading",
                            "microphone_model_invalid", "microphone_invalid_configuration",
                            "microphone_transcription_failed"})
            allowed.update({"web_busy", "web_disabled", "web_closed", "web_invalid_configuration",
                            "web_provider_unavailable", "web_redirect_rejected", "web_rate_limited",
                            "web_unsupported_content", "web_response_too_large", "web_invalid_response",
                            "web_empty_content", "web_missing_search_key"})
            code = str(exc) if str(exc) in allowed else "service_unavailable"
            return JsonResponse({"error": code}, status=409 if code.endswith("busy") else 503)
        except Exception as exc:
            logger.warning("Servicio del asistente no disponible (%s).", type(exc).__name__)
            return JsonResponse({"error": "service_unavailable"}, status=503)
    return wrapped


def body(request: ASGIRequest, keys: set[str], *, optional: set[str] | None = None) -> dict[str, object]:
    """Acepta solo las claves de cada contrato; no interpreta parametros extra."""
    if request.content_type != "application/json":
        raise ValueError("json_required")
    try:
        value = json.loads(request.body)
    except (ValueError, RecursionError):
        raise ValueError("invalid_json") from None
    if not isinstance(value, dict) or not keys <= set(value) or not set(value) <= keys | (optional or set()):
        raise ValueError("invalid_fields")
    return value


@require_GET
@private
async def capabilities(request: ASGIRequest, services: AssistantServices) -> JsonResponse:
    from .operator import principal
    _, scopes = principal(request)
    return JsonResponse({"language": await services.language.probe(), "voice": services.voice.status,
                         'storage': {'encrypted': services.audit.cipher is not None,
                                     'algorithm': 'AES-256-GCM' if services.audit.cipher else None},
                         "microphone": services.microphone.status,
                         "conversation": {"auto_start": services.conversation_auto_start,
                                          "vad": "silero_local", "max_seconds": 15},
                         "internet": services.internet.status,
                         "operator": {"context": "session_memory", "context_ttl_seconds": 900,
                                      "memory": services.conversations.status,
                                      "panel_capacity": 8, "documents": "local_pdf_text", "max_pdf_bytes": 8 * 1024 * 1024,
                                      "camera": "browser_explicit_consent", "camera_allowed": "camera:use" in scopes,
                                      "briefing_enabled": services.operator.preferences.value["briefing_enabled"]},
                         "identity": {"name": "T.H.O.R.N", "version": RELEASE, "local_ai_only": services.local_ai_only,
                                      "profile": services.language.model, "base_model": "Qwen2.5 1.5B" if services.language.model in {MODEL_PROFILE, "thorn:bastion8", "qwen2.5:1.5b"} else "modelo local configurado",
                                      "training": "pretrained_local_profile", "self_trained": False},
                         "actions": {"enabled": services.actions.enabled, "catalog": services.actions.catalog(),
                                     "requires": "Windows sin elevacion y auditoria MySQL"},
                         "audit": {"enabled": services.audit.enabled},
                         "alerts": {"active": list(services.alerts.active.values()),
                                    "errors": services.alerts.errors}})


@require_GET
@private
async def alerts(request: ASGIRequest, services: AssistantServices) -> JsonResponse:
    return JsonResponse({"active": list(services.alerts.active.values()),
                         "rules": services.alerts.configuration, "errors": services.alerts.errors,
                         "durable": services.audit.enabled})


@require_POST
@private
async def configure_alerts(request: ASGIRequest, services: AssistantServices) -> JsonResponse:
    data = body(request, {"rules"})
    await services.alerts.configure(data["rules"])
    return JsonResponse({"saved": True, "rules": services.alerts.configuration})


@require_GET
@private
async def history(request: ASGIRequest, services: AssistantServices) -> JsonResponse:
    if not services.audit.enabled:
        raise RuntimeError("database_required")
    try:
        before = int(request.GET["before"]) if "before" in request.GET else None
        limit = int(request.GET.get("limit", "30"))
    except ValueError:
        raise ValueError("invalid_page") from None
    rows = await services.audit.history(before=before, limit=limit)
    return JsonResponse({"records": rows, "durable": True,
                         "next_before": rows[-1]["id"] if len(rows) == limit else None})


@require_POST
@private
async def prepare_action(request: ASGIRequest, services: AssistantServices) -> JsonResponse:
    data = body(request, {"action_id"})
    if not isinstance(data["action_id"], str):
        raise ValueError("invalid_action_id")
    return JsonResponse(services.actions.prepare(data["action_id"]), status=202)


@require_POST
@private
async def confirm_action(request: ASGIRequest, services: AssistantServices) -> JsonResponse:
    data = body(request, {"confirmation_id"})
    confirmation = data["confirmation_id"]
    if not isinstance(confirmation, str) or not 1 <= len(confirmation) <= 128:
        raise ValueError("invalid_confirmation_id")
    return JsonResponse(await services.actions.confirm(confirmation))


@require_POST
@private
async def chat(request: ASGIRequest, services: AssistantServices) -> JsonResponse:
    data = body(request, {"prompt"})
    prompt = data["prompt"]
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 2000:
        raise ValueError("invalid_prompt")
    from .operator import principal
    owner, scopes = principal(request)
    result = await services.operator.chat(owner, prompt, scopes)
    return JsonResponse(result)


@require_GET
@private
async def network_status(request: ASGIRequest, services: AssistantServices) -> JsonResponse:
    """Solo cache local: ni refrescar el HUD ni leer estado contacta Internet."""
    return JsonResponse({"sensor": services.network.status, "snapshot": services.network.snapshot,
                         "internet": services.internet.status})


@require_POST
@private
async def network_diagnose(request: ASGIRequest, services: AssistantServices) -> JsonResponse:
    data = body(request, {"internet_consent"})
    return JsonResponse(await services.internet.diagnose(consent=data["internet_consent"], identity=uuid4().hex))


@require_POST
@private
async def web_search(request: ASGIRequest, services: AssistantServices) -> JsonResponse:
    data = body(request, {"query", "internet_consent"})
    return await web_result(request, services, data, read=False)


@require_POST
@private
async def web_read(request: ASGIRequest, services: AssistantServices) -> JsonResponse:
    data = body(request, {"url", "internet_consent"})
    return await web_result(request, services, data, read=True)


async def web_result(request: ASGIRequest, services: AssistantServices, data: dict[str, object], *, read: bool) -> JsonResponse:
    from .operator import principal
    owner, scopes = principal(request)
    state = services.operator.store.state(owner, touch=True)
    generation = state.generation
    epoch = await services.operator.memory_epoch(scopes)
    async def work() -> dict[str, object]:
        if read:
            result = await services.internet.read(data["url"], consent=data["internet_consent"], identity=uuid4().hex)
            prompt = "Resume la pagina adjunta y senala sus limites."
        else:
            result = await services.internet.search(data["query"], consent=data["internet_consent"], identity=uuid4().hex)
            prompt = "Resume los resultados para: " + data["query"]
        answer = await services.web_answer(result, prompt)
        services.operator.ensure_current(owner, state, generation)
        saved = await services.operator.save_turn(prompt, answer["reply"], "internet", scopes, epoch)
        services.operator.ensure_current(owner, state, generation)
        services.operator.store.remember(owner, state, prompt, answer["reply"])
        panel = services.operator.store.add_panel(owner, "sources", "Fuentes consultadas", {
            "sources": answer["sources"], "text": answer["reply"]}, source="internet")
        return {**answer, "panel": panel, "ui_commands": [], "memory": saved}
    return JsonResponse(await services.operator.run(owner, work))


@require_GET
@private
async def memory(request: ASGIRequest, services: AssistantServices) -> JsonResponse:
    if not services.audit.enabled:
        raise RuntimeError("database_required")
    return JsonResponse({"notes": await services.audit.memory(), "durable": True, "capacity": 20})


@require_POST
@private
async def save_memory(request: ASGIRequest, services: AssistantServices) -> JsonResponse:
    data = body(request, {"text"})
    if not isinstance(data["text"], str):
        raise ValueError("invalid_memory_text")
    identity = await services.audit.update_memory(text=data["text"])
    return JsonResponse({"saved": True, "id": identity})


@require_POST
@private
async def delete_memory(request: ASGIRequest, services: AssistantServices) -> JsonResponse:
    data = body(request, {"id"})
    if not isinstance(data["id"], str):
        raise ValueError("invalid_memory_id")
    await services.audit.update_memory(delete_id=data["id"])
    return JsonResponse({"deleted": True})


@require_POST
@private
async def speech(request: ASGIRequest, services: AssistantServices) -> HttpResponse:
    """Devuelve audio autenticado; no guarda ni expone enlaces publicos al archivo."""
    data = body(request, {"text", "cloud_consent"})
    if not isinstance(data["text"], str):
        raise ValueError("voice_invalid_text")
    identity = uuid4().hex
    audio = await services.voice.speak(data["text"], cloud_consent=data["cloud_consent"], request_id=identity)
    response = HttpResponse(audio, content_type=getattr(services.voice, "content_type", "audio/mpeg"))
    response["X-Thorn-Voice-Provider"] = services.voice.status["provider"]
    response["Cache-Control"] = "no-store"
    response["X-Content-Type-Options"] = "nosniff"
    response["X-Thorn-Speech-ID"] = identity
    return response


@require_POST
@private
async def transcribe(request: ASGIRequest, services: AssistantServices) -> JsonResponse:
    """Audio local autenticado; no persiste grabaciones ni envia audio a la nube."""
    if request.headers.get("X-Thorn-Microphone-Consent") != "local":
        raise ValueError("microphone_local_consent_required")
    if request.content_type != "application/octet-stream":
        raise ValueError("microphone_pcm_required")
    result = await services.microphone.transcribe(request.body, local_consent=True, request_id=uuid4().hex)
    response = JsonResponse(result)
    response["Cache-Control"] = "no-store"
    response["X-Content-Type-Options"] = "nosniff"
    return response
