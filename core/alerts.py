"""Alertas por presion sostenida, con histeresis y muestras autenticas."""

from __future__ import annotations

import asyncio
import math
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Callable

from .audit import AuditStore
from .event_bus import EventBus, EventBusClosedError
from .events import EventPriority, EventType, ThornEvent


@dataclass(frozen=True, slots=True)
class AlertRule:
    """Sube tras hold_seconds; se recupera solo por debajo de clear_percent."""

    threshold_percent: float
    clear_percent: float
    hold_seconds: float
    enabled: bool = True

    @classmethod
    def parse(cls, value: object) -> AlertRule:
        if not isinstance(value, dict) or set(value) != {
            "threshold_percent", "clear_percent", "hold_seconds", "enabled",
        }:
            raise ValueError("invalid_alert_rule")
        numbers = [value[key] for key in ("threshold_percent", "clear_percent", "hold_seconds")]
        if any(isinstance(n, bool) or not isinstance(n, (int, float)) or not math.isfinite(n) for n in numbers):
            raise ValueError("invalid_alert_number")
        high, low, hold = numbers
        if not 0 <= low < high <= 100 or not 0 <= hold <= 600 or not isinstance(value["enabled"], bool):
            raise ValueError("invalid_alert_bounds")
        return cls(high, low, hold, value["enabled"])


class AlertEngine:
    """Evalua una vez por muestra y emite solo cambios, nunca alertas repetidas."""

    FIELDS = {"cpu": "cpu_percent", "memory": "memory_percent", "disk": "disk_percent"}

    def __init__(self, bus: EventBus, audit: AuditStore, *, max_gap: float = 6,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.bus, self.audit, self.clock, self.max_gap = bus, audit, clock, max_gap
        self.rules = {"cpu": AlertRule(90, 80, 15), "memory": AlertRule(90, 85, 8),
                      "disk": AlertRule(90, 87, 0)}
        self.active: dict[str, dict[str, object]] = {}
        self._since: dict[str, float] = {}
        self._last_time: float | None = None
        self._last_sample = ""
        self._lock = asyncio.Lock()
        self.errors = 0
        bus.subscribe(EventType.SYSTEM_SNAPSHOT, self.on_sample)

    @property
    def configuration(self) -> dict[str, dict[str, object]]:
        return {name: asdict(rule) for name, rule in self.rules.items()}

    async def configure(self, value: object, *, persist: bool = True) -> None:
        """Valida todo antes de guardar o cambiar una regla."""
        if not isinstance(value, dict) or set(value) != set(self.FIELDS):
            raise ValueError("invalid_alert_configuration")
        parsed = {name: AlertRule.parse(rule) for name, rule in value.items()}
        async with self._lock:
            if persist:
                await self.audit.save_setting("alerts", value)
            self.rules = parsed
            self._since.clear()
            # La nueva regla se evalua en la siguiente muestra; no inventa recuperaciones.

    async def on_sample(self, event: ThornEvent) -> None:
        """Solo el sensor interno puede originar estas alertas."""
        if event.source != "sensor.system":
            return
        sample = event.payload.get("sampled_at")
        if not isinstance(sample, str):
            return
        try:
            stamp = datetime.fromisoformat(sample)
            age = (datetime.now(timezone.utc) - stamp).total_seconds()
        except (ValueError, TypeError):
            return
        if not -1 <= age <= self.max_gap:
            return
        async with self._lock:
            if sample <= self._last_sample:
                return
            now = self.clock()
            if self._last_time is not None and now - self._last_time > self.max_gap:
                self._since.clear()
            self._last_sample, self._last_time = sample, now
            for name, field in self.FIELDS.items():
                rule, value = self.rules[name], event.payload.get(field)
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 100:
                    self._since.pop(name, None)
                    continue
                if name in self.active:
                    self.active[name]["percent"] = value
                    if not rule.enabled or value <= rule.clear_percent:
                        await self._transition(name, value, "recovered")
                    continue
                if not rule.enabled or value < rule.threshold_percent:
                    self._since.pop(name, None)
                    continue
                since = self._since.setdefault(name, now)
                if now - since >= rule.hold_seconds:
                    await self._transition(name, value, "active")

    async def _transition(self, resource: str, value: float, state: str) -> None:
        event = ThornEvent(EventType.SYSTEM_ALERT, "alerts.system", {
            "resource": resource, "percent": value, "state": state,
        }, priority=EventPriority.HIGH)
        record = {"resource": resource, "percent": value, "state": state,
                  "created_at": event.created_at.isoformat(), "event_id": event.event_id}
        if state == "active":
            self.active[resource] = record
        else:
            self.active.pop(resource, None)
            self._since.pop(resource, None)
        try:
            await self.audit.append("system.alert", state, f"Presion de {resource}: {state}",
                                    {"resource": resource, "percent": value})
        except Exception:
            self.errors += 1
        try:
            await self.bus.publish(event)
        except (asyncio.QueueFull, EventBusClosedError):
            self.errors += 1
