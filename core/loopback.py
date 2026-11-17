"""Servicios propios en esta PC (Ollama, SearXNG): solo loopback, sin DNS ni credenciales."""

from __future__ import annotations

from urllib.parse import urlsplit


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
