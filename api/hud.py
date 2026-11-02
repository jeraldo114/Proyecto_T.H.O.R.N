"""Sirve el HUD compilado y sus recursos locales sin I/O en el event loop."""

from __future__ import annotations

import asyncio
import json
from functools import lru_cache
from pathlib import Path

from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.http import require_GET
from core.paths import RESOURCE_ROOT

DIST = RESOURCE_ROOT / "frontend" / "dist"
MIME_TYPES = {
    ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8", ".woff2": "font/woff2", ".woff": "font/woff",
    ".mjs": "text/javascript; charset=utf-8", ".wasm": "application/wasm",
    ".onnx": "application/octet-stream",
    ".task": "application/octet-stream",
    ".bcmap": "application/octet-stream", ".pfb": "application/octet-stream",
    ".ttf": "font/ttf", ".png": "image/png", ".svg": "image/svg+xml",
}


@lru_cache(maxsize=32)
def _read_asset(name: str) -> tuple[bytes, str]:
    """Consulta una lista cerrada; el cache se renueva reiniciando el servidor."""
    allowed = json.loads((DIST / "manifest.json").read_text(encoding="utf-8"))
    if not isinstance(allowed, list) or not all(isinstance(item, str) for item in allowed):
        raise ValueError("El manifiesto debe ser una lista de nombres.")
    if name not in allowed or Path(name).name != name:
        raise FileNotFoundError(name)
    suffix = Path(name).suffix
    if suffix not in MIME_TYPES:
        raise FileNotFoundError(name)
    path = DIST / name
    if path.is_symlink() or path.resolve().parent != DIST.resolve():
        raise FileNotFoundError(name)
    return path.read_bytes(), MIME_TYPES[suffix]


async def _response(name: str) -> HttpResponse:
    try:
        content, content_type = await asyncio.to_thread(_read_asset, name)
    except (OSError, ValueError):
        return JsonResponse({"error": "hud_asset_unavailable"}, status=404)
    response = HttpResponse(content, content_type=content_type)
    response["Content-Security-Policy"] = (
        "default-src 'none'; script-src 'self' 'wasm-unsafe-eval'; worker-src 'self' blob:; style-src 'self' 'unsafe-inline'; "
        "font-src 'self' blob:; connect-src 'self'; img-src 'self' data: https://tile.openstreetmap.org; media-src 'self' blob:; "
        "base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
    )
    response["Referrer-Policy"] = "no-referrer"
    response["X-Frame-Options"] = "DENY"
    response["Permissions-Policy"] = "microphone=(self), camera=(self), display-capture=()"
    return response


@require_GET
async def index(request: HttpRequest) -> HttpResponse:
    """Abre la consola, nunca incluye el token privado en el HTML."""
    return await _response("index.html")


@require_GET
async def asset(request: HttpRequest, name: str) -> HttpResponse:
    """Entrega solo recursos conocidos del build, no archivos del proyecto."""
    return await _response(name)
