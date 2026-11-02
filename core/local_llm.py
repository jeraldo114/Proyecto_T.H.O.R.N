"""Cliente Ollama asincrono, restringido a loopback y sin herramientas ejecutables."""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from urllib.parse import urlsplit

import httpx

from .actions import CATALOG
from .audit import AuditStore
from .personality import system_prompt
from .brain_host import LocalBrainHost


def validate_endpoint(value: str) -> str:
    """No permite DNS, proxies, credenciales, redireccionamientos ni hosts remotos."""
    try:
        url = urlsplit(value)
        port = url.port
    except ValueError:
        raise ValueError("invalid_local_endpoint") from None
    if (url.scheme != "http" or url.hostname not in {"127.0.0.1", "::1"}
            or not port or url.username or url.password or url.path not in {"", "/"}
            or url.query or url.fragment):
        raise ValueError("local_loopback_endpoint_required")
    return value.rstrip("/")


class LocalLanguageModel:
    """Concurrencia maxima uno. El JSON del modelo solo puede sugerir una accion."""

    def __init__(self, audit: AuditStore, *, endpoint: str, model: str,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        endpoint = validate_endpoint(endpoint)
        if not re.fullmatch(r"[A-Za-z0-9_.:/-]{1,120}", model) or re.search(r"(?:^|[-/:])cloud(?:$|[:/])", model):
            raise ValueError("invalid_local_model")
        self.model, self.audit = model, audit
        self.client = httpx.AsyncClient(base_url=endpoint, trust_env=False, follow_redirects=False,
                                       timeout=httpx.Timeout(60, connect=2), transport=transport)
        self.state = "unchecked"
        self._checked = float("-inf")
        self._probe_lock = asyncio.Lock()
        self._query_lock = asyncio.Lock()
        self._closed = False
        self.audit_errors = 0
        self.endpoint = endpoint
        self.host = LocalBrainHost()

    async def start(self) -> None:
        """Puede iniciar el motor ya instalado; no instala pesos ni consume APIs."""
        if os.getenv("THORN_BRAIN_AUTOSTART", "0") == "1" and self.endpoint == "http://127.0.0.1:11434":
            await self.host.start()
            await self.probe()

    @property
    def status(self) -> dict[str, object]:
        return {"state": self.state, "model": self.model, "busy": self._query_lock.locked(),
                "available": self.state == "ready" and not self._closed,
                "audit_errors": self.audit_errors}

    async def _json(self, method: str, path: str, *, body: object = None, timeout: float = 60) -> object:
        async with asyncio.timeout(timeout):
            async with self.client.stream(method, path, json=body) as response:
                response.raise_for_status()
                raw = bytearray()
                async for chunk in response.aiter_bytes():
                    raw.extend(chunk)
                    if len(raw) > 256 * 1024:
                        raise ValueError("model_response_too_large")
                return json.loads(raw)

    async def probe(self) -> dict[str, object]:
        """Consulta modelos instalados, con cache de 15 s para no cargar el HUD."""
        async with self._probe_lock:
            if time.monotonic() - self._checked < 15 or self._closed:
                return self.status
            try:
                response = await self._json("GET", "/api/tags", timeout=2)
                if not isinstance(response, dict) or not isinstance(response.get("models"), list):
                    raise ValueError("invalid_models_response")
                candidates = [item for item in response["models"] if isinstance(item, dict)
                              and item.get("name") == self.model]
                # Los modelos remotos de Ollama no son aceptables para este modulo.
                local = any(not item.get("remote_host") and not item.get("remote_model")
                            for item in candidates)
                self.state = "ready" if local else "model_missing"
            except (httpx.HTTPError, TimeoutError, ValueError, RecursionError):
                self.state = "engine_unavailable"
            self._checked = time.monotonic()
            return self.status

    async def _audit(self, state: str, query_id: str) -> None:
        try:
            await self.audit.append("assistant.query", state, "Consulta al modelo local",
                                    {"query_id": query_id, "model": self.model})
        except Exception:
            self.audit_errors += 1

    async def ask(self, prompt: str, *, context: dict[str, object], query_id: str,
                  history: list[dict[str, str]] | None = None) -> dict[str, object]:
        """Usa solo contexto real entregado por el runtime. Nunca invoca el controlador OS."""
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 2000:
            raise ValueError("invalid_prompt")
        if self._query_lock.locked():
            raise RuntimeError("model_busy")
        # NOTA DEL OPERADOR: una consulta a la vez para cuidar la RAM del F15.
        # La espera de Ollama cede el loop; sensores y API siguen atendiendo.
        async with self._query_lock:
            await self.probe()
            if self.state != "ready" or self._closed:
                raise RuntimeError(self.state)
            await self._audit("started", query_id)
            untrusted_document = "document_sources_untrusted" in context or "web_sources_untrusted" in context
            schema = {"type": "object", "additionalProperties": False,
                      "properties": {"reply": {"type": "string"}, "suggested_action": {
                          "type": ["string", "null"], "enum": [None] if untrusted_document else [None, *CATALOG]}},
                      "required": ["reply", "suggested_action"]}
            bounded = dict(context)
            memories = list(bounded.get("conversation_memory_untrusted", []))
            if "conversation_memory_untrusted" in bounded:
                bounded["conversation_memory_untrusted"] = memories
            system = system_prompt(bounded)
            # NOTA DEL OPERADOR: el cuaderno puede crecer en disco; el contexto
            # de Qwen no. Sacamos extractos de menor prioridad antes de llenar RAM.
            while memories and len(system.encode("utf-8")) + len(prompt.encode("utf-8")) > 6400:
                memories.pop()
                system = system_prompt(bounded)
            # El historial llega del almacen de la sesion autenticada, no de JSON
            # que permita al cliente introducir mensajes con rol system/tool.
            recent: list[dict[str, str]] = []
            budget = max(0, 6800 - len(system.encode("utf-8")) - len(prompt.encode("utf-8")))
            for pair in reversed([history[index:index + 2] for index in range(0, len(history or []), 2)]):
                if len(pair) != 2 or [item.get("role") for item in pair] != ["user", "assistant"]:
                    continue
                size = sum(len(item["content"].encode("utf-8")) for item in pair)
                if size > budget:
                    break
                recent[0:0] = [{"role": item["role"], "content": item["content"]} for item in pair]
                budget -= size
            try:
                result = await self._json("POST", "/api/chat", body={
                    "model": self.model, "stream": False, "format": schema,
                    "keep_alive": "2m", "options": {"num_ctx": 2048, "num_predict": 384,
                                                        "temperature": 0.2},
                    "messages": [{"role": "system", "content": system}, *recent,
                                 {"role": "user", "content": prompt.strip()}],
                })
                if not isinstance(result, dict) or result.get("done") is not True or result.get("remote_host") or result.get("remote_model"):
                    raise ValueError("incomplete_model_response")
                message = result.get("message")
                if not isinstance(message, dict) or not isinstance(message.get("content"), str):
                    raise ValueError("invalid_model_response")
                answer = json.loads(message["content"])
                if not isinstance(answer, dict) or set(answer) != {"reply", "suggested_action"}:
                    raise ValueError("invalid_model_response")
                reply, action = answer["reply"], answer["suggested_action"]
                # Un JSON bien formado aun necesita validacion. El modelo
                # propone; tu confirmas desde el HUD antes de cualquier accion.
                if not isinstance(reply, str) or not reply.strip() or len(reply) > 4000:
                    raise ValueError("invalid_model_reply")
                if action is not None and (not isinstance(action, str) or action not in CATALOG):
                    raise ValueError("model_action_not_allowed")
                if untrusted_document and action is not None:
                    raise ValueError("model_action_not_allowed")
            except asyncio.CancelledError:
                await self._audit("cancelled", query_id)
                raise
            except (httpx.HTTPError, TimeoutError, ValueError, RecursionError):
                await self._audit("failed", query_id)
                self.state = "unchecked"
                self._checked = float("-inf")
                raise RuntimeError("model_query_failed") from None
            await self._audit("answered", query_id)
            return {"reply": reply, "suggested_action": action, "query_id": query_id,
                    "model": self.model, "executed": False}

    async def stop(self) -> None:
        self._closed = True
        async with self._query_lock:
            await self.client.aclose()
        await self.host.stop()
