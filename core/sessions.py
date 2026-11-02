"""Sesiones opacas locales: secretos aleatorios, hashes en RAM y permisos cerrados."""

from __future__ import annotations

import hashlib
import secrets
import time
from dataclasses import dataclass, field
from typing import Callable
from uuid import uuid4

ALL_SCOPES = frozenset({"telemetry:read", "assistant:chat", "voice:speak", "microphone:transcribe",
    "memory:read", "memory:write", "events:publish", "actions:prepare", "actions:confirm", "network:read", "network:query",
    "workspace:read", "workspace:write", "documents:read", "documents:write", "preferences:read", "preferences:write", "camera:use"})


@dataclass(slots=True)
class Session:
    identity: str
    digest: bytes = field(repr=False)
    scopes: frozenset[str]
    expires: float
    absolute: float
    previous: bytes | None = field(default=None, repr=False)
    previous_until: float = 0


class SessionAuthority:
    """Estado solo del loop propietario. No hace I/O ni acepta permisos del modelo."""

    def __init__(self, *, clock: Callable[[], float] = time.monotonic, ttl: int = 900,
                 absolute_ttl: int = 86400, grace: int = 30, max_sessions: int = 32) -> None:
        if ttl < 60 or absolute_ttl < ttl or not 0 <= grace < ttl or max_sessions < 1:
            raise ValueError("invalid_session_policy")
        self.clock, self.ttl, self.absolute_ttl = clock, ttl, absolute_ttl
        self.grace, self.max_sessions = grace, max_sessions
        self.sessions: dict[str, Session] = {}
        self.closed = False
        self._cleanup: list[Callable[[str], None]] = []

    def on_release(self, callback: Callable[[str], None]) -> None:
        """Registra limpieza sin I/O; se ejecuta en el loop propietario."""
        self._cleanup.append(callback)

    def _released(self, identity: str) -> None:
        for callback in self._cleanup:
            callback(identity)

    @staticmethod
    def digest(token: str) -> bytes:
        return hashlib.sha256(token.encode("utf-8")).digest()

    def prune(self) -> None:
        now = self.clock()
        expired = [key for key, item in self.sessions.items() if now >= min(item.expires, item.absolute)]
        self.sessions = {key: item for key, item in self.sessions.items() if now < min(item.expires, item.absolute)}
        for identity in expired:
            self._released(identity)

    def response(self, session: Session, token: str) -> dict[str, object]:
        return {"access_token": token, "token_type": "Bearer", "expires_in": max(0, int(session.expires - self.clock())),
                "absolute_expires_in": max(0, int(session.absolute - self.clock())), "session_id": session.identity,
                "scopes": sorted(session.scopes)}

    def issue(self, scopes: frozenset[str] = ALL_SCOPES) -> dict[str, object]:
        """Solo el host nativo o una autenticacion de autoridad pueden invocarlo."""
        if self.closed or not scopes or not scopes <= ALL_SCOPES:
            raise ValueError("session_issue_not_allowed")
        self.prune()
        if len(self.sessions) >= self.max_sessions:
            raise RuntimeError("session_limit")
        # NOTA DEL OPERADOR: estos tokens son llaves de acceso locales, no
        # creditos de una IA. Se generan con azar criptografico; guardamos su hash.
        token = "thn_" + secrets.token_urlsafe(48)
        now = self.clock()
        session = Session(uuid4().hex, self.digest(token), scopes, now + self.ttl, now + self.absolute_ttl)
        self.sessions[session.identity] = session
        return self.response(session, token)

    def find(self, token: str, *, current_only: bool = False) -> Session | None:
        if self.closed or not isinstance(token, str) or not token.startswith("thn_") or len(token) > 256:
            return None
        self.prune()
        digest, now = self.digest(token), self.clock()
        for session in self.sessions.values():
            if secrets.compare_digest(digest, session.digest):
                return session
            if not current_only and session.previous and now < session.previous_until and secrets.compare_digest(digest, session.previous):
                return session
        return None

    def renew(self, token: str) -> dict[str, object]:
        session = self.find(token, current_only=True)
        if session is None:
            raise ValueError("invalid_session")
        now = self.clock()
        # La ventana de gracia deja terminar peticiones que ya salieron con
        # el token anterior. Renovar no prolonga el limite absoluto de la sesion.
        session.previous, session.previous_until = session.digest, now + self.grace
        token = "thn_" + secrets.token_urlsafe(48)
        session.digest = self.digest(token)
        session.expires = min(now + self.ttl, session.absolute)
        return self.response(session, token)

    def revoke(self, token: str) -> bool:
        session = self.find(token)
        if session is None:
            return False
        del self.sessions[session.identity]
        self._released(session.identity)
        return True

    def revoke_identity(self, identity: str) -> None:
        """Canal interno del host; no se expone por HTTP ni al modelo."""
        if self.sessions.pop(identity, None) is not None:
            self._released(identity)

    def close(self) -> None:
        self.closed = True
        for identity in list(self.sessions):
            self._released(identity)
        self.sessions.clear()


def required_scope(path: str, method: str) -> str:
    if path.startswith("/api/v1/documents/"):
        return "documents:read" if method == "GET" or path.endswith("/ask/") else "documents:write"
    if path.startswith("/api/v1/preferences"):
        return "preferences:read" if method == "GET" else "preferences:write"
    if path.startswith("/api/v1/workspace"):
        return "workspace:read" if method == "GET" else "workspace:write"
    if path.startswith("/api/v1/conversation/"):
        if path.endswith("/history/"):
            return "memory:read"
        if path.endswith("/forget/"):
            return "memory:write"
        return "assistant:chat"
    if path.startswith("/api/v1/maps/"):
        return "network:query"
    if path.startswith("/api/v1/briefing/"):
        return "telemetry:read"
    if path.startswith("/api/v1/actions/"):
        return "actions:confirm" if path.endswith("/confirm/") else "actions:prepare"
    if path.startswith("/api/v1/web/") or path == "/api/v1/network/diagnose/":
        return "network:query"
    if path == "/api/v1/network/":
        return "network:read"
    if path.startswith("/api/v1/memory"):
        return "memory:read" if method == "GET" else "memory:write"
    return {"/api/v1/chat/": "assistant:chat", "/api/v1/voice/speech/": "voice:speak",
            "/api/v1/microphone/transcribe/": "microphone:transcribe", "/api/v1/events/": "events:publish",
            "/api/v1/alerts/configure/": "events:publish"}.get(path, "telemetry:read")


def authorize(headers: list[tuple[bytes, bytes]], *, root: str, authority: SessionAuthority | None,
              path: str, method: str) -> int | None:
    """Comparten esta comprobacion Django y el limite ASGI previo a recibir audio."""
    values = [value for key, value in headers if key.lower() == b"authorization"]
    if not root:
        return 503
    if len(values) != 1 or len(values[0]) > 512 or not values[0].startswith(b"Bearer "):
        return 401
    value = values[0][7:]
    if secrets.compare_digest(value, root.encode("utf-8")):
        return None
    try:
        token = value.decode("ascii")
    except UnicodeError:
        return 401
    session = authority.find(token) if authority else None
    if session is None:
        return 401
    return None if required_scope(path, method) in session.scopes else 403
