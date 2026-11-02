"""Busqueda y lectura consentidas; las fuentes nunca reciben contexto privado."""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Any, AsyncIterator, Callable
from urllib.parse import urlencode

import httpcore
import httpx

from .audit import AuditStore
from .event_bus import EventBus, EventBusClosedError
from .events import ThornEvent
from .public_web import PublicWebClient, public_url


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

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self.IGNORED:
            self.skip.append(tag)
        if tag == "title":
            self.in_title = True

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


def extract_page(body: bytes, mime: str) -> tuple[str, str]:
    """Parser de la biblioteca estandar; se ejecuta en worker por limite de CPU."""
    raw = body.decode("utf-8", errors="replace")
    if mime == "text/plain":
        return "Documento de texto", clean(raw, 12000)
    parser = PageText(); parser.feed(raw); parser.close()
    return clean(" ".join(parser.title), 200), clean(" ".join(parser.parts), 12000)


class InternetService:
    """Sin navegacion autonoma: una operacion, 60/h, consentimiento por solicitud."""

    def __init__(self, audit: AuditStore, bus: EventBus, *, enabled: bool | None = None,
                 provider: str | None = None, brave_key: str | None = None,
                 client: PublicWebClient | None = None, clock: Callable[[], float] = time.monotonic) -> None:
        flag = os.getenv("THORN_INTERNET_ENABLED", "0")
        self.enabled = flag == "1" if enabled is None else enabled
        self.provider = provider if provider is not None else os.getenv("THORN_SEARCH_PROVIDER", "wikipedia")
        self.brave_key = brave_key if brave_key is not None else os.getenv("BRAVE_SEARCH_API_KEY", "")
        self.invalid = (enabled is None and flag not in {"0", "1"}) or self.provider not in {"wikipedia", "brave"}
        if self.brave_key and not re.fullmatch(r"[A-Za-z0-9_.-]{1,512}", self.brave_key):
            self.invalid = True
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

    @asynccontextmanager
    async def operation(self, operation: str, consent: bool, identity: str) -> AsyncIterator[None]:
        if consent is not True:
            raise ValueError("web_internet_consent_required")
        if not self.status["enabled"]:
            raise RuntimeError("web_" + str(self.status["state"]))
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
            except (httpcore.NetworkError, httpcore.ProtocolError, httpcore.TimeoutException,
                    OSError, TimeoutError, UnicodeError, json.JSONDecodeError, RecursionError):
                await self._record(operation, "failed", identity)
                raise RuntimeError("web_provider_unavailable") from None
            except Exception:
                await self._record(operation, "failed", identity)
                raise
            else:
                await self._record(operation, "completed", identity)

    async def search(self, query: str, *, consent: bool, identity: str) -> dict[str, Any]:
        if not isinstance(query, str) or not query.strip() or len(query) > 240 or any(ord(char) < 32 for char in query):
            raise ValueError("web_invalid_query")
        async with self.operation("search", consent, identity):
            if self.provider == "brave":
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
                value = item.get("url") if self.provider == "brave" else item.get("fullurl")
                try:
                    safe_url = public_url(value)
                except ValueError:
                    continue
                if self.provider == "wikipedia" and httpx.URL(safe_url).host != "es.wikipedia.org":
                    continue
                title = clean(item.get("title"), 200)
                excerpt = clean(item.get("description") if self.provider == "brave" else item.get("extract"), 700)
                if title:
                    sources.append({"title": title, "url": safe_url, "excerpt": excerpt, "retrieved_at": stamp, "kind": "search"})
            return {"sources": sources, "provider": self.provider, "retrieved_at": stamp,
                    "request_id": identity, "truncated": len(results) > 3}

    async def read(self, url: str, *, consent: bool, identity: str) -> dict[str, Any]:
        safe_url = public_url(url)
        async with self.operation("read", consent, identity):
            raw, mime, final_url = await self.client.get(safe_url)
            title, content = await asyncio.to_thread(extract_page, raw, mime)
            if not content:
                raise RuntimeError("web_empty_content")
            stamp = datetime.now(timezone.utc).isoformat()
            return {"sources": [{"title": title or httpx.URL(final_url).host, "url": final_url,
                                  "excerpt": content[:6000], "retrieved_at": stamp, "kind": "page"}],
                    "provider": "https", "retrieved_at": stamp, "request_id": identity,
                    "truncated": len(content) > 6000}

    async def diagnose(self, *, consent: bool, identity: str) -> dict[str, object]:
        """Prueba HTTPS fija, no un ping ICMP ni una prueba de todo Internet."""
        async with self.operation("diagnose", consent, identity):
            started = time.monotonic()
            addresses = await self.client.backend.resolver("example.com", 443)
            from .public_web import public_address
            if not addresses or not all(public_address(address) for address in addresses):
                raise ValueError("web_public_https_required")
            dns_ms = (time.monotonic() - started) * 1000
            started = time.monotonic()
            await self.client.get("https://example.com/")
            return {"target": "https://example.com/", "dns_ms": round(dns_ms, 2),
                    "https_ms": round((time.monotonic() - started) * 1000, 2),
                    "checked_at": datetime.now(timezone.utc).isoformat(), "reachable": True,
                    "measurement": "HTTPS round trip, not ICMP"}

    async def stop(self) -> None:
        self.closed = True
        async with self.lock:
            await self.client.stop()
