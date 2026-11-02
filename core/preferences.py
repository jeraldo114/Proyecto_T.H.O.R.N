"""Preferencias explicitas, validadas y protegidas por la persistencia existente."""

from __future__ import annotations

import asyncio
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .audit import AuditStore

DEFAULTS = {"display_name": "", "briefing_enabled": True,
            "memory_context_enabled": True, "timezone": "America/Santiago"}


def validate_preferences(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != set(DEFAULTS):
        raise ValueError("invalid_preferences")
    name = value["display_name"]
    zone = value["timezone"]
    if (not isinstance(name, str) or len(name) > 60 or any(ord(c) < 32 for c in name)
            or type(value["briefing_enabled"]) is not bool or type(value["memory_context_enabled"]) is not bool
            or not isinstance(zone, str) or not 1 <= len(zone) <= 80):
        raise ValueError("invalid_preferences")
    try:
        ZoneInfo(zone)
    except (ValueError, ZoneInfoNotFoundError):
        raise ValueError("invalid_timezone") from None
    return {**value, "display_name": name.strip()}


class OperatorPreferences:
    """No guarda audio, historial de chat ni autorizaciones de camara/nube."""

    def __init__(self, audit: AuditStore) -> None:
        self.audit = audit
        self.value: dict[str, object] = dict(DEFAULTS)
        self.zone = ZoneInfo("America/Santiago")
        self.lock = asyncio.Lock()

    async def start(self) -> None:
        saved = await self.audit.load_setting("operator_preferences")
        if saved is not None:
            self.value = await asyncio.to_thread(validate_preferences, saved)
            self.zone = await asyncio.to_thread(ZoneInfo, self.value["timezone"])

    async def update(self, value: object) -> dict[str, object]:
        checked = await asyncio.to_thread(validate_preferences, value)
        async with self.lock:
            if not self.audit.enabled:
                raise RuntimeError("database_required")
            await self.audit.save_setting("operator_preferences", checked)
            self.value = checked
            self.zone = await asyncio.to_thread(ZoneInfo, checked["timezone"])
            return dict(self.value)
