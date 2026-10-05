"""Puente ASGI: comparte un runtime entre el servidor y las vistas Django."""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from core.runtime import ThornRuntime

logger = logging.getLogger(__name__)
ASGIMessage = dict[str, Any]
ASGIScope = dict[str, Any]
Receive = Callable[[], Awaitable[ASGIMessage]]
Send = Callable[[ASGIMessage], Awaitable[None]]
ASGIApplication = Callable[[ASGIScope, Receive, Send], Awaitable[None]]
RuntimeFactory = Callable[[], ThornRuntime]


class ThornASGI:
    """Traduce startup/shutdown de ASGI al ciclo de vida del nucleo.

    Django procesa HTTP, pero no administra el lifespan de nuestros workers.
    Este puente crea el runtime al arrancar y lo inyecta en el scope de cada
    peticion. Usar un worker de servidor: el bus vive dentro de este proceso.
    """

    def __init__(
        self, django_app: ASGIApplication, runtime_factory: RuntimeFactory,
        *, max_request_bytes: int = 16 * 1024,
    ) -> None:
        self._django_app = django_app
        self._runtime_factory = runtime_factory
        self._max_request_bytes = max_request_bytes
        self._runtime: ThornRuntime | None = None

    async def __call__(self, scope: ASGIScope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            await self._lifespan(receive, send)
        elif scope["type"] == "http":
            await self._http(scope, receive, send)
        elif scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1003})
        else:
            raise ValueError("Tipo de scope ASGI no soportado.")

    async def _http(self, scope: ASGIScope, receive: Receive, send: Send) -> None:
        """Recibe hasta 16 KiB en memoria antes de crear los buffers de Django."""
        body_buffer = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            if message["type"] != "http.request":
                raise ValueError("Mensaje ASGI HTTP inesperado.")
            chunk = message.get("body", b"")
            # Se cuentan bytes reales: Content-Length no es una fuente confiable.
            if len(body_buffer) + len(chunk) > self._max_request_bytes:
                body = json.dumps({"error": "request_too_large"}).encode("utf-8")
                await send({
                    "type": "http.response.start", "status": 413,
                    "headers": [(b"content-type", b"application/json")],
                })
                await send({"type": "http.response.body", "body": body})
                return
            body_buffer.extend(chunk)
            if not message.get("more_body", False):
                break

        replayed = False

        async def buffered_receive() -> ASGIMessage:
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": bytes(body_buffer)}
            # Django sigue recibiendo la desconexion del cliente normalmente.
            return await receive()

        request_scope = dict(scope)
        request_scope["thorn.runtime"] = self._runtime
        await self._django_app(request_scope, buffered_receive, send)

    async def _lifespan(self, receive: Receive, send: Send) -> None:
        """Responde al servidor solo cuando el arranque o cierre ha terminado."""
        try:
            while True:
                message = await receive()
                if message["type"] == "lifespan.startup":
                    try:
                        self._runtime = self._runtime_factory()
                        await self._runtime.start()
                    except Exception:
                        logger.exception("No se pudo iniciar el runtime ASGI.")
                        await send({
                            "type": "lifespan.startup.failed",
                            "message": "Fallo al iniciar T.H.O.R.N; revisa los logs.",
                        })
                        return
                    await send({"type": "lifespan.startup.complete"})
                elif message["type"] == "lifespan.shutdown":
                    runtime, self._runtime = self._runtime, None
                    try:
                        if runtime is not None:
                            await runtime.stop()
                    except Exception:
                        logger.exception("No se pudo cerrar el runtime ASGI.")
                        await send({
                            "type": "lifespan.shutdown.failed",
                            "message": "Fallo al cerrar T.H.O.R.N; revisa los logs.",
                        })
                    else:
                        await send({"type": "lifespan.shutdown.complete"})
                    return
        finally:
            if self._runtime is not None:
                # Tambien limpia si el servidor cancela el lifespan inesperadamente.
                try:
                    await self._runtime.stop()
                finally:
                    self._runtime = None
