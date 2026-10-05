"""Contratos de eventos compartidos por los modulos de T.H.O.R.N."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import IntEnum, StrEnum
from types import MappingProxyType
from typing import Any
from uuid import uuid4


class EventType(StrEnum):
    """Temas iniciales; los modulos pueden usar otros nombres de tema."""

    RUNTIME_STARTED = "runtime.started"
    RUNTIME_STOPPING = "runtime.stopping"
    COMMAND_RECEIVED = "command.received"
    SYSTEM_ALERT = "system.alert"
    MODULE_ERROR = "module.error"


class EventPriority(IntEnum):
    """Un valor menor se despacha antes, sin interrumpir trabajo activo."""

    CRITICAL = 0
    HIGH = 10
    NORMAL = 20
    LOW = 30


@dataclass(frozen=True, slots=True)
class ThornEvent:
    """Mensaje con identidad, origen y correlacion entre operaciones.

    El payload se copia y protege en su primer nivel. Sus valores anidados
    deben tratarse como inmutables por todos los modulos.
    """

    # event_type indica que paso; source indica que modulo lo detecto.
    event_type: str
    source: str
    payload: Mapping[str, Any] = field(default_factory=dict)
    priority: EventPriority = EventPriority.NORMAL
    correlation_id: str | None = None
    event_id: str = field(default_factory=lambda: uuid4().hex)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        if not self.event_type.strip() or not self.source.strip():
            raise ValueError("El tipo y el origen del evento no pueden estar vacios.")
        object.__setattr__(self, "payload", MappingProxyType(dict(self.payload)))
