"""Contratos Bastion 9; resultados, documentos y preferencias por sesion."""

from __future__ import annotations

import secrets
from urllib.parse import unquote

from django.conf import settings
from django.core.handlers.asgi import ASGIRequest
from django.http import HttpResponse, JsonResponse
from django.views.decorators.http import require_GET, require_POST

from core.services import AssistantServices
from core.documents import question_excerpts
from core.sessions import ALL_SCOPES
from .assistant import body, private
from .sessions import credential
from .views import _runtime


def principal(request: ASGIRequest) -> tuple[str, frozenset[str]]:
    """Una renovacion conserva identidad; otra sesion nunca comparte contexto."""
    token = credential(request)
    runtime = _runtime(request)
    if token and secrets.compare_digest(token, settings.THORN_API_TOKEN):
        return "authority", ALL_SCOPES
    session = runtime.sessions.find(token) if runtime and token else None
    if session is None:
        raise ValueError("invalid_session")
    return session.identity, session.scopes


@require_GET
@private
async def workspace(request: ASGIRequest, services: AssistantServices) -> JsonResponse:
    owner, _ = principal(request)
    return JsonResponse(services.operator.store.snapshot(owner))


@require_POST
@private
async def change_panel(request: ASGIRequest, services: AssistantServices) -> JsonResponse:
    data = body(request, {"id", "operation"}, optional={"position"})
    if not isinstance(data["id"], str) or not isinstance(data["operation"], str):
        raise ValueError("invalid_panel")
    owner, _ = principal(request)
    previous = services.operator.store.state(owner).panels.get(data["id"])
    result = services.operator.store.update_panel(owner, data["id"], data["operation"], data.get("position"))
    if data["operation"] == "close" and previous and previous["kind"] == "document":
        identity = previous["data"]["id"]
        services.operator.documents.get(owner, identity)
        services.operator.documents.items.pop(identity, None)
    return JsonResponse(result)


@require_GET
@private
async def briefing(request: ASGIRequest, services: AssistantServices) -> JsonResponse:
    return JsonResponse(await services.operator.briefing(principal(request)[0]))


@require_GET
@private
async def preferences(request: ASGIRequest, services: AssistantServices) -> JsonResponse:
    return JsonResponse({"preferences": dict(services.operator.preferences.value), "durable": services.audit.enabled})


@require_POST
@private
async def save_preferences(request: ASGIRequest, services: AssistantServices) -> JsonResponse:
    data = body(request, {"preferences"})
    return JsonResponse({"preferences": await services.operator.preferences.update(data["preferences"]), "saved": True})


@require_POST
@private
async def cancel(request: ASGIRequest, services: AssistantServices) -> JsonResponse:
    body(request, set())
    owner, _ = principal(request)
    return JsonResponse({"cancelled": services.operator.store.cancel(owner), "scope": "current_session"})


@require_POST
@private
async def reset(request: ASGIRequest, services: AssistantServices) -> JsonResponse:
    body(request, set())
    owner, _ = principal(request)
    services.operator.store.cancel(owner, clear=True)
    services.operator.documents.release(owner)
    return JsonResponse({"cleared": True, "scope": "current_session"})


@require_POST
@private
async def map_lookup(request: ASGIRequest, services: AssistantServices) -> JsonResponse:
    data = body(request, {"query", "internet_consent"})
    return JsonResponse(await services.operator.map(principal(request)[0], data["query"], data["internet_consent"]))


@require_POST
@private
async def upload(request: ASGIRequest, services: AssistantServices) -> JsonResponse:
    if request.content_type != "application/pdf" or request.headers.get("X-Thorn-Document-Consent") != "local":
        raise ValueError("document_local_consent_required")
    name = request.headers.get("X-Thorn-Document-Name", "documento.pdf")
    if len(name) > 400:
        raise ValueError("document_invalid_name")
    try:
        name = unquote(name, errors="strict")
    except UnicodeError:
        raise ValueError("document_invalid_name") from None
    return JsonResponse(await services.operator.upload(principal(request)[0], request.body, name), status=201)


@require_GET
@private
async def download(request: ASGIRequest, services: AssistantServices, identity: str) -> HttpResponse:
    document = services.operator.documents.get(principal(request)[0], identity)
    response = HttpResponse(document.raw, content_type="application/pdf")
    response["Content-Disposition"] = 'attachment; filename="thorn-document-' + identity + '.pdf"'
    response["Cache-Control"] = "no-store"
    response["X-Content-Type-Options"] = "nosniff"
    return response


@require_POST
@private
async def ask_document(request: ASGIRequest, services: AssistantServices) -> JsonResponse:
    data = body(request, {"id", "prompt"})
    owner, scopes = principal(request)
    if "assistant:chat" not in scopes:
        return JsonResponse({"error": "insufficient_scope"}, status=403)
    if not isinstance(data["id"], str) or not isinstance(data["prompt"], str) or not 1 <= len(data["prompt"].strip()) <= 2000:
        raise ValueError("document_invalid_question")
    document = services.operator.documents.get(owner, data["id"])
    state = services.operator.store.state(owner, touch=True)
    generation = state.generation
    async def work() -> dict[str, object]:
        from uuid import uuid4
        excerpts = question_excerpts(document.result["pages"], data["prompt"])
        answer = await services.language.ask(data["prompt"], query_id=uuid4().hex, context={
            "document_sources_untrusted": excerpts,
            "truncated": document.result["truncated"] or
                sum(len(page["text"]) for page in excerpts) < sum(len(page["text"]) for page in document.result["pages"])})
        services.operator.ensure_current(owner, state, generation)
        panel = services.operator.store.add_panel(owner, "answer", "Consulta al documento", {"text": answer["reply"], "document_id": data["id"]})
        return {**answer, "panel": panel, "source": "document", "ui_commands": []}
    return JsonResponse(await services.operator.run(owner, work))
