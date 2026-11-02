"""GET HTTPS publico: DNS validado y fijado al socket, sin cookies ni proxies."""

from __future__ import annotations

import asyncio
import ipaddress
import socket
import ssl
from collections.abc import Awaitable, Callable
from urllib.parse import urljoin

import httpcore
import httpx

MAX_BODY = 1024 * 1024
Resolver = Callable[[str, int], Awaitable[list[str]]]


def public_address(value: str) -> bool:
    """Excluye redes privadas, metadata, multicast y traducciones IPv6 ambiguas."""
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    if not address.is_global or address.is_multicast or address.is_reserved:
        return False
    if isinstance(address, ipaddress.IPv6Address):
        if address.ipv4_mapped or address.sixtofour or address.teredo:
            return False
        if any(address in ipaddress.ip_network(prefix) for prefix in ("64:ff9b::/96", "64:ff9b:1::/48")):
            return False
    return True


def public_url(value: object) -> str:
    """Canoniza con el mismo parser usado por el cliente; nunca acepta credenciales."""
    if not isinstance(value, str) or not value or len(value) > 2048:
        raise ValueError("web_invalid_url")
    if any(ord(char) < 33 or ord(char) == 127 or char == "\\" for char in value):
        raise ValueError("web_invalid_url")
    try:
        url = httpx.URL(value)
        host = url.raw_host.decode("ascii").lower()
        if (url.scheme != "https" or url.port not in {None, 443} or url.userinfo
                or not host or host.endswith(".") or "%" in host or len(host) > 253):
            raise ValueError("web_public_https_required")
        try:
            ipaddress.ip_address(host)
        except ValueError:
            if "." not in host or host.endswith((".local", ".localhost", ".lan", ".internal", ".home")):
                raise ValueError("web_public_https_required")
        else:
            if not public_address(host):
                raise ValueError("web_public_https_required")
        return str(url.copy_with(fragment=None))
    except (httpx.InvalidURL, UnicodeError):
        raise ValueError("web_invalid_url") from None


async def resolve_public(host: str, port: int) -> list[str]:
    """getaddrinfo usa el executor del loop, no el hilo HTTP."""
    async with asyncio.timeout(3):
        records = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return list(dict.fromkeys(record[4][0] for record in records))


class PublicNetworkBackend(httpcore.AsyncNetworkBackend):
    """Conecta solo a IPs publicas ya resueltas; TLS conserva el hostname original."""

    def __init__(self, resolver: Resolver = resolve_public,
                 backend: httpcore.AsyncNetworkBackend | None = None) -> None:
        self.resolver = resolver
        self.backend = backend if backend is not None else httpcore.AnyIOBackend()

    async def connect_tcp(self, host: str, port: int, timeout: float | None = None,
                          local_address: str | None = None, socket_options: object = None) -> httpcore.AsyncNetworkStream:
        if port != 443:
            raise ValueError("web_public_https_required")
        async with asyncio.timeout(timeout if timeout is not None else 4):
            addresses = await self.resolver(host, port)
            if not addresses or len(addresses) > 32 or not all(public_address(ip) for ip in addresses):
                raise ValueError("web_public_https_required")
            addresses.sort(key=lambda value: ipaddress.ip_address(value).version)
            # No se vuelve a resolver el nombre entre validacion y conexion.
            for address in addresses[:4]:
                try:
                    attempt_timeout = max(0.25, (timeout if timeout is not None else 4) / min(4, len(addresses)))
                    return await self.backend.connect_tcp(address, port, timeout=attempt_timeout,
                                                          socket_options=socket_options)
                except (httpcore.ConnectError, httpcore.ConnectTimeout):
                    continue
            raise httpcore.ConnectError("public_connection_failed")

    async def connect_unix_socket(self, *args: object, **kwargs: object) -> httpcore.AsyncNetworkStream:
        raise ValueError("web_public_https_required")

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)


class PublicWebClient:
    """Cuerpo <= 1 MiB, tres saltos HTTPS validados, TLS y plazo total de 15 s."""

    def __init__(self, *, backend: PublicNetworkBackend | None = None) -> None:
        self.backend = backend if backend is not None else PublicNetworkBackend()
        self.pool: httpcore.AsyncConnectionPool | None = None
        self._init_lock = asyncio.Lock()
        self.closed = False

    async def get(self, value: str, *, json_only: bool = False,
                  brave_key: str = "") -> tuple[bytes, str, str]:
        url = public_url(value)
        if self.closed:
            raise RuntimeError("web_closed")
        async with asyncio.timeout(15):
            async with self._init_lock:
                if self.closed:
                    raise RuntimeError("web_closed")
                if self.pool is None:
                    context = await asyncio.to_thread(ssl.create_default_context)
                    self.pool = httpcore.AsyncConnectionPool(ssl_context=context, network_backend=self.backend,
                        max_connections=2, max_keepalive_connections=1, keepalive_expiry=5, retries=0)
            for hop in range(4):
                headers = [(b"User-Agent", b"THORN-Bastion9/0.9 (+https://github.com/jeraldo114/Proyecto_T.H.O.R.N)"),
                           (b"Accept-Encoding", b"identity"),
                           (b"Accept", b"application/json" if json_only else b"text/html, text/plain")]
                if brave_key:
                    if httpx.URL(url).host != "api.search.brave.com":
                        raise ValueError("web_public_https_required")
                    headers.append((b"X-Subscription-Token", brave_key.encode("ascii")))
                async with self.pool.stream("GET", url, headers=headers,
                    extensions={"timeout": {"connect": 4, "read": 4, "write": 4, "pool": 1}}) as response:
                    meta = {key.lower(): val for key, val in response.headers}
                    if response.status in {301, 302, 303, 307, 308}:
                        if brave_key or hop == 3 or b"location" not in meta:
                            raise RuntimeError("web_redirect_rejected")
                        url = public_url(urljoin(url, meta[b"location"].decode("ascii")))
                        continue
                    if response.status == 429:
                        raise RuntimeError("web_rate_limited")
                    if response.status != 200:
                        raise RuntimeError("web_provider_unavailable")
                    mime = meta.get(b"content-type", b"").decode("ascii", errors="ignore").split(";")[0].strip().lower()
                    allowed = {"application/json"} if json_only else {"text/html", "text/plain", "application/xhtml+xml"}
                    if mime not in allowed or meta.get(b"content-encoding", b"identity").lower() != b"identity":
                        raise RuntimeError("web_unsupported_content")
                    length = meta.get(b"content-length")
                    if length and (not length.isdigit() or int(length) > MAX_BODY):
                        raise RuntimeError("web_response_too_large")
                    body = bytearray()
                    async for chunk in response.aiter_stream():
                        if len(body) + len(chunk) > MAX_BODY:
                            raise RuntimeError("web_response_too_large")
                        body.extend(chunk)
                    return bytes(body), mime, url
            raise RuntimeError("web_redirect_rejected")

    async def stop(self) -> None:
        self.closed = True
        async with self._init_lock:
            if self.pool is not None:
                await self.pool.aclose()
