"""Validacion de host sin middleware sincronico en la ruta HTTP."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from django.core.exceptions import DisallowedHost
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.utils.decorators import async_only_middleware

AsyncResponse = Callable[[HttpRequest], Awaitable[HttpResponse]]


@async_only_middleware
def local_host_middleware(get_response: AsyncResponse) -> AsyncResponse:
    """Comprueba ALLOWED_HOSTS; con middleware minimo Django no lo hace solo."""
    async def middleware(request: HttpRequest) -> HttpResponse:
        try:
            request.get_host()
        except DisallowedHost:
            return JsonResponse({"error": "invalid_host"}, status=400)
        response = await get_response(request)
        # NOTA DEL OPERADOR: las respuestas privadas no se quedan en cache.
        # Validar el Host ayuda al acceso local; los permisos van por separado.
        response["Cache-Control"] = "no-store"
        response["X-Content-Type-Options"] = "nosniff"
        return response

    return middleware
