"""Busqueda y lectura consentidas; las fuentes nunca reciben contexto privado."""

from __future__ import annotations

import asyncio
import json
import os
import re
import ssl
import time
from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Any, AsyncIterator, Callable
from urllib.parse import urlencode

import httpcore
import httpx

from core.memory.audit import AuditStore
from core.event_bus import EventBus, EventBusClosedError
from core.events import ThornEvent
from core.loopback import validate_endpoint
from core.network.public_web import PublicWebClient, public_address, public_url

PROVIDERS = {"wikipedia", "brave", "searxng"}
# Host publico de cada proveedor para el diagnostico; SearXNG corre en esta PC.
PROVIDER_HOSTS = {"wikipedia": "es.wikipedia.org", "brave": "api.search.brave.com"}
SEARXNG_DEFAULT = "http://127.0.0.1:8888"
MAX_LOCAL_SEARCH = 1024 * 1024


class PageText(HTMLParser):
    """Extrae texto, no ejecuta HTML, CSS, scripts, formularios ni enlaces."""

    IGNORED = {"script", "style", "nav", "footer", "header", "form", "svg", "canvas", "iframe", "noscript", "template"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title: list[str] = []
        self.skip: list[str] = []
        self.in_title = False
        self.characters = 0
        self.published_at: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self.IGNORED:
            self.skip.append(tag)
        if tag == "title":
            self.in_title = True
        if tag == "meta" and not self.skip and self.published_at is None:
            meta = dict(attrs)
            if meta.get("property") == "article:published_time" or meta.get("itemprop") == "datePublished":
                value = meta.get("content") or ""
                if re.fullmatch(r"\d{4}-\d{2}-\d{2}(?:T[^\s]{1,30})?", value):
                    try:
                        self.published_at = datetime.fromisoformat(value).date().isoformat()
                    except ValueError:
                        pass

    def handle_endtag(self, tag: str) -> None:
        if self.skip and tag == self.skip[-1]:
            self.skip.pop()
        if tag == "title":
            self.in_title = False

    def handle_data(self, data: str) -> None:
        if self.skip:
            return
        if self.in_title:
            self.title.append(data[:200])
        elif self.characters < 12000:
            part = data[:12000 - self.characters]
            self.parts.append(part); self.characters += len(part)


def clean(value: object, limit: int) -> str:
    """Normaliza solo texto; ningun fragmento se interpreta como marcado en el HUD."""
    return " ".join(value.split())[:limit] if isinstance(value, str) else ""


def extract_page(body: bytes, mime: str) -> tuple[str, str, str | None]:
    """Parser de la biblioteca estandar; se ejecuta en worker por limite de CPU."""
    raw = body.decode("utf-8", errors="replace")
    if mime == "text/plain":
        return "Documento de texto", clean(raw, 12000), None
    parser = PageText(); parser.feed(raw); parser.close()
    return clean(" ".join(parser.title), 200), clean(" ".join(parser.parts), 12000), parser.published_at


class InternetService:
    """Sin navegacion autonoma: una operacion, 60/h, consentimiento por solicitud."""

    def __init__(self, audit: AuditStore, bus: EventBus, *, enabled: bool | None = None,
                 provider: str | None = None, brave_key: str | None = None,
                 client: PublicWebClient | None = None, clock: Callable[[], float] = time.monotonic,
                 searxng_url: str | None = None, searxng_transport: httpx.AsyncBaseTransport | None = None,
                 offline: Callable[[], bool] | None = None) -> None:
        flag = os.getenv("THORN_INTERNET_ENABLED", "0")
        self.enabled = flag == "1" if enabled is None else enabled
        self.provider = provider if provider is not None else os.getenv("THORN_SEARCH_PROVIDER", "wikipedia")
        self.brave_key = brave_key if brave_key is not None else os.getenv("BRAVE_SEARCH_API_KEY", "")
        self.invalid = (enabled is None and flag not in {"0", "1"}) or self.provider not in PROVIDERS
        if self.brave_key and not re.fullmatch(r"[A-Za-z0-9_.-]{1,512}", self.brave_key):
            self.invalid = True
        # NOTA DEL OPERADOR: SearXNG es un buscador que instalas tu (contenedor local).
        # Sin claves ni pagos; consulta motores publicos y nos responde solo por loopback.
        self.searxng: httpx.AsyncClient | None = None
        if self.provider == "searxng":
            try:
                url = validate_endpoint(searxng_url if searxng_url is not None
                                        else os.getenv("THORN_SEARXNG_URL", SEARXNG_DEFAULT))
            except ValueError:
                self.invalid = True
            else:
                self.searxng = httpx.AsyncClient(base_url=url, trust_env=False, follow_redirects=False,
                                                 timeout=httpx.Timeout(10, connect=2), transport=searxng_transport)
        # Sin interfaz con direccion utilizable no esperamos 15 s a que falle el DNS.
        self.offline = offline
        self.audit, self.bus = audit, bus
        self.client = client if client is not None else PublicWebClient()
        self.clock = clock
        self.lock = asyncio.Lock()
        self.calls: deque[float] = deque()
        self.last_started = float("-inf")
        self.closed = False
        self.paid_search_blocked = os.getenv("THORN_LOCAL_AI_ONLY", "1") != "0" and self.provider == "brave"
        self.audit_errors = self.event_errors = 0

    @property
    def status(self) -> dict[str, object]:
        return {"enabled": self.enabled and not self.invalid and not self.closed,
                "provider": self.provider, "search_available": self.enabled and not self.invalid and not self.closed
                and not self.paid_search_blocked and (self.provider != "brave" or bool(self.brave_key)),
                "state": "closed" if self.closed else "invalid_configuration" if self.invalid else
                "disabled" if not self.enabled else "paid_search_blocked" if self.paid_search_blocked else "missing_search_key" if self.provider == "brave" and not self.brave_key else "configured",
                "busy": self.lock.locked(), "requires_consent": True, "read_only": True,
                "max_query_characters": 240, "audit_errors": self.audit_errors, "event_errors": self.event_errors}

    async def _record(self, operation: str, state: str, identity: str) -> None:
        # Sin consultas, URLs, snippets, IPs, claves ni contenido remoto en auditoria.
        try:
            async with asyncio.timeout(2):
                await self.audit.append("network.web", state, "Operacion web de solo lectura",
                    {"operation": operation, "request_id": identity, "provider": self.provider})
        except Exception:
            self.audit_errors += 1
        try:
            async with asyncio.timeout(0.1):
                await self.bus.publish(ThornEvent("network.web." + state, "network.internet",
                    {"operation": operation}, correlation_id=identity))
        except (EventBusClosedError, TimeoutError):
            self.event_errors += 1

    def is_offline(self) -> bool:
        try:
            return bool(self.offline and self.offline())
        except Exception:
            return False

    @asynccontextmanager
    async def operation(self, operation: str, consent: bool, identity: str, *,
                        allow_offline: bool = False) -> AsyncIterator[None]:
        if consent is not True:
            raise ValueError("web_internet_consent_required")
        if not self.status["enabled"]:
            raise RuntimeError("web_" + str(self.status["state"]))
        if not allow_offline and self.is_offline():
            raise RuntimeError("web_offline")
        if self.lock.locked():
            raise RuntimeError("web_busy")
        async with self.lock:
            now = self.clock()
            while self.calls and now - self.calls[0] >= 3600:
                self.calls.popleft()
            if now - self.last_started < 2 or len(self.calls) >= 60:
                raise RuntimeError("web_rate_limited")
            self.calls.append(now); self.last_started = now
            await self._record(operation, "started", identity)
            try:
                yield
            except asyncio.CancelledError:
                await self._record(operation, "cancelled", identity)
                raise
            except (httpcore.NetworkError, httpcore.ProtocolError, httpcore.TimeoutException, httpx.HTTPError,
                    OSError, TimeoutError, UnicodeError, json.JSONDecodeError, RecursionError):
                await self._record(operation, "failed", identity)
                raise RuntimeError("web_provider_unavailable") from None
            except Exception:
                await self._record(operation, "failed", identity)
                raise
            else:
                await self._record(operation, "completed", identity)

    async def search(self, query: str, *, consent: bool, identity: str, investigate: bool = False) -> dict[str, Any]:
        if not isinstance(query, str) or not query.strip() or len(query) > 240 or any(ord(char) < 32 for char in query):
            raise ValueError("web_invalid_query")
        async with self.operation("research" if investigate else "search", consent, identity):
            if self.provider == "searxng":
                data = await self._local_search(query.strip())
                results = data.get("results") if isinstance(data, dict) else None
            elif self.provider == "brave":
                if self.paid_search_blocked:
                    raise RuntimeError("web_paid_provider_blocked")
                if not self.brave_key:
                    raise RuntimeError("web_missing_search_key")
                url = "https://api.search.brave.com/res/v1/web/search?" + urlencode(
                    {"q": query.strip(), "count": 3, "search_lang": "es", "safesearch": "strict"})
                raw, _, _ = await self.client.get(url, json_only=True, brave_key=self.brave_key)
                data = json.loads(raw)
                web = data.get("web", {}) if isinstance(data, dict) else None
                results = web.get("results", []) if isinstance(web, dict) else None
            else:
                url = "https://es.wikipedia.org/w/api.php?" + urlencode({
                    "action": "query", "generator": "search", "gsrsearch": query.strip(), "gsrnamespace": 0,
                    "gsrlimit": 3, "prop": "extracts|info", "inprop": "url", "explaintext": 1,
                    "exintro": 1, "exchars": 700, "exlimit": 3, "format": "json", "formatversion": 2})
                raw, _, _ = await self.client.get(url, json_only=True)
                data = json.loads(raw)
                if not isinstance(data, dict) or "error" in data:
                    raise RuntimeError("web_invalid_response")
                response_query = data.get("query", {})
                results = response_query.get("pages", []) if isinstance(response_query, dict) else None
                if isinstance(results, list):
                    results.sort(key=lambda item: item.get("index", 99) if isinstance(item, dict) else 99)
            if not isinstance(results, list):
                raise RuntimeError("web_invalid_response")
            stamp = datetime.now(timezone.utc).isoformat()
            sources = []
            for item in results[:3]:
                if not isinstance(item, dict):
                    continue
                value = item.get("fullurl") if self.provider == "wikipedia" else item.get("url")
                try:
                    safe_url = public_url(value)
                except ValueError:
                    continue
                if self.provider == "wikipedia" and httpx.URL(safe_url).host != "es.wikipedia.org":
                    continue
                title = clean(item.get("title"), 200)
                excerpt = clean({"brave": item.get("description"), "searxng": item.get("content")}.get(
                    self.provider, item.get("extract")), 700)
                if title:
                    sources.append({"title": title, "url": safe_url, "excerpt": excerpt, "retrieved_at": stamp, "kind": "search"})
            if investigate:
                # Solo URLs devueltas por el proveedor; el cliente vuelve a validar DNS y cada redirección.
                async def enrich(source: dict[str, Any]) -> None:
                    try:
                        raw, mime, final = await self.client.get(source["url"])
                        title, content, published = await asyncio.to_thread(extract_page, raw, mime)
                    except (RuntimeError, ValueError, OSError, TimeoutError, httpcore.NetworkError,
                            httpcore.ProtocolError, httpcore.TimeoutException):
                        return
                    if content:
                        source.update(title=title or source["title"], url=final,
                                      excerpt=content[:1400], kind="page", published_at=published)
                await asyncio.gather(*(enrich(source) for source in sources))
            return {"sources": sources, "provider": self.provider, "retrieved_at": stamp,
                    "request_id": identity, "truncated": len(results) > 3}

    async def read(self, url: str, *, consent: bool, identity: str) -> dict[str, Any]:
        safe_url = public_url(url)
        async with self.operation("read", consent, identity):
            raw, mime, final_url = await self.client.get(safe_url)
            title, content, published = await asyncio.to_thread(extract_page, raw, mime)
            if not content:
                raise RuntimeError("web_empty_content")
            stamp = datetime.now(timezone.utc).isoformat()
            return {"sources": [{"title": title or httpx.URL(final_url).host, "url": final_url,
                                  "excerpt": content[:6000], "retrieved_at": stamp, "kind": "page", "published_at": published}],
                    "provider": "https", "retrieved_at": stamp, "request_id": identity,
                    "truncated": len(content) > 6000}

    async def _local_search(self, query: str) -> object:
        """SearXNG propio por loopback; el JSON debe estar habilitado en su settings.yml."""
        if self.searxng is None:
            raise RuntimeError("web_invalid_configuration")
        params = {"q": query, "format": "json", "language": "es", "safesearch": 1}
        try:
            async with self.searxng.stream("GET", "/search", params=params) as response:
                if response.status_code == 403:
                    raise RuntimeError("web_local_search_json_disabled")
                if response.status_code != 200:
                    raise RuntimeError("web_provider_unavailable")
                raw = bytearray()
                async for chunk in response.aiter_bytes():
                    raw.extend(chunk)
                    if len(raw) > MAX_LOCAL_SEARCH:
                        raise RuntimeError("web_response_too_large")
        except httpx.ConnectError:
            raise RuntimeError("web_local_search_unavailable") from None
        return json.loads(raw)

    async def _probe(self, host: str) -> dict[str, object]:
        """Mide DNS y HTTPS por separado y dice en que etapa fallo."""
        check: dict[str, object] = {"host": host, "dns_ms": None, "https_ms": None,
                                    "reachable": False, "failed_stage": None}
        started = time.monotonic()
        try:
            addresses = await self.client.backend.resolver(host, 443)
        except (OSError, TimeoutError):
            check["failed_stage"] = "dns"
            return check
        check["dns_ms"] = round((time.monotonic() - started) * 1000, 2)
        if not addresses or not all(public_address(address) for address in addresses):
            # Un nombre publico que responde con una IP privada: portal cautivo o DNS filtrado.
            check["failed_stage"] = "dns_private"
            return check
        started = time.monotonic()
        try:
            await self.client.get(f"https://{host}/")
        except RuntimeError as error:
            if str(error) == "web_closed":
                raise
            # El servidor respondio por HTTPS aunque no con una pagina util: la ruta funciona.
            check["failed_stage"] = "http"
        except (httpcore.TimeoutException, TimeoutError):
            check["failed_stage"] = "timeout"
            return check
        except (httpcore.ConnectError, httpcore.NetworkError, httpcore.ProtocolError, OSError) as error:
            cause, tls = error, False
            while cause is not None and not tls:
                tls = isinstance(cause, ssl.SSLError) or "CERTIFICATE" in str(cause).upper()
                cause = cause.__cause__ or cause.__context__
            check["failed_stage"] = "tls" if tls else "connect"
            return check
        check["https_ms"] = round((time.monotonic() - started) * 1000, 2)
        check["reachable"] = True
        return check

    async def diagnose(self, *, consent: bool, identity: str) -> dict[str, object]:
        """HTTPS a example.com y al proveedor de busqueda; no es ICMP ni una prueba de todo Internet."""
        async with self.operation("diagnose", consent, identity, allow_offline=True):
            stamp = datetime.now(timezone.utc).isoformat()
            base = {"target": "https://example.com/", "checked_at": stamp, "measurement": "HTTPS round trip, not ICMP"}
            if self.is_offline():
                return {**base, "reachable": False, "failed_stage": "offline", "dns_ms": None, "https_ms": None,
                        "checks": []}
            hosts = ["example.com"]
            if PROVIDER_HOSTS.get(self.provider, "example.com") != "example.com":
                hosts.append(PROVIDER_HOSTS[self.provider])
            checks = await asyncio.gather(*(self._probe(host) for host in hosts))
            primary = checks[0]
            return {**base, "reachable": primary["reachable"], "failed_stage": primary["failed_stage"],
                    "dns_ms": primary["dns_ms"], "https_ms": primary["https_ms"], "checks": checks}

    async def stop(self) -> None:
        self.closed = True
        async with self.lock:
            await self.client.stop()
            if self.searxng is not None:
                await self.searxng.aclose()
