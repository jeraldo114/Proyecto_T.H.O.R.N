"""Contexto y resultados efimeros: propietario por sesion, limites y cancelacion."""

from __future__ import annotations

import asyncio
import copy
import json
import math
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable
from uuid import uuid4

PANEL_KINDS = frozenset({"answer", "system", "sources", "memory", "document", "map", "briefing"})


@dataclass(slots=True)
class InteractionState:
    """Nunca contiene el token de acceso ni una autoridad persistente."""

    touched: float
    turns: list[dict[str, str]] = field(default_factory=list)
    panels: dict[str, dict[str, object]] = field(default_factory=dict)
    revision: int = 0
    generation: int = 0
    phase: str = "ready"


class InteractionStore:
    """Estado del event loop: 32 sesiones y una autoridad de compatibilidad."""

    def __init__(self, *, clock: Callable[[], float] = time.monotonic, ttl: float = 900,
                 capacity: int = 33, on_release: Callable[[str], None] | None = None) -> None:
        self.clock, self.ttl, self.capacity = clock, ttl, capacity
        self.on_release = on_release
        self.states: dict[str, InteractionState] = {}
        self.tasks: dict[str, asyncio.Task[object]] = {}
        self.retiring: set[asyncio.Task[object]] = set()

    def release(self, owner: str) -> None:
        """Revocar una sesion impide que un resultado tardio la reconstruya."""
        self.states.pop(owner, None)
        task = self.tasks.pop(owner, None)
        if task is not None and not task.done():
            task.cancel()
            self.retiring.add(task)
            task.add_done_callback(self.retiring.discard)
        if self.on_release is not None:
            self.on_release(owner)

    def prune(self) -> None:
        now = self.clock()
        for owner, state in list(self.states.items()):
            if now - state.touched >= self.ttl:
                self.release(owner)

    def state(self, owner: str, *, touch: bool = False) -> InteractionState:
        self.prune()
        if not owner or len(owner) > 128:
            raise ValueError("invalid_interaction_owner")
        state = self.states.get(owner)
        if state is None:
            if len(self.states) >= self.capacity:
                raise RuntimeError("interaction_capacity")
            state = self.states[owner] = InteractionState(self.clock())
        if touch:
            state.touched = self.clock()
        return state

    def remember(self, owner: str, state: InteractionState, prompt: str, reply: str) -> None:
        """Solo confirma turnos completos de una sesion que todavia existe."""
        if self.states.get(owner) is not state:
            return
        # NOTA DEL OPERADOR: recordar un turno no equivale a guardarlo en MySQL.
        # Ocho mensajes breves bastan para referencias recientes sin llenar el F15.
        state.turns.extend(({"role": "user", "content": prompt[:1200]},
                            {"role": "assistant", "content": reply[:1600]}))
        while len(state.turns) > 8 or len(json.dumps(state.turns).encode()) > 9000:
            del state.turns[:2]
        state.touched = self.clock()

    def add_panel(self, owner: str, kind: str, title: str, data: dict[str, object],
                  *, source: str = "local") -> dict[str, object]:
        if kind not in PANEL_KINDS or not isinstance(title, str) or len(title) > 120:
            raise ValueError("invalid_panel")
        if len(json.dumps(data, ensure_ascii=False, allow_nan=False).encode()) > 28000:
            raise ValueError("panel_too_large")
        state = self.state(owner, touch=True)
        if len(state.panels) >= 8:
            state.panels.pop(next(iter(state.panels)))
        panel = {"id": uuid4().hex, "kind": kind, "title": title, "source": source,
                 "created_at": datetime.now(timezone.utc).isoformat(), "state": "open",
                 "position": {"x": 0.1, "y": 0.1}, "data": copy.deepcopy(data)}
        state.panels[panel["id"]] = panel
        state.revision += 1
        return copy.deepcopy(panel)

    def update_panel(self, owner: str, identity: str, operation: str,
                     position: object = None) -> dict[str, object]:
        state = self.state(owner, touch=True)
        if identity not in state.panels:
            raise ValueError("panel_not_found")
        if operation == "close":
            del state.panels[identity]
        elif operation in {"minimize", "restore", "focus"}:
            panel = state.panels.pop(identity)
            panel["state"] = "minimized" if operation == "minimize" else "open"
            state.panels[identity] = panel
        elif operation == "move":
            if (not isinstance(position, dict) or set(position) != {"x", "y"} or
                    any(type(value) not in {int, float} or not math.isfinite(value) or
                        not 0 <= value <= 1 for value in position.values())):
                raise ValueError("invalid_panel_position")
            state.panels[identity]["position"] = dict(position)
        else:
            raise ValueError("invalid_panel_operation")
        state.revision += 1
        return self.snapshot(owner)

    def snapshot(self, owner: str) -> dict[str, object]:
        state = self.state(owner)
        return {"revision": state.revision, "phase": state.phase,
                "context_messages": len(state.turns), "durable": False,
                "panels": copy.deepcopy(list(state.panels.values())), "capacity": 8,
                "context_ttl_seconds": self.ttl}

    def cancel(self, owner: str, *, clear: bool = False) -> bool:
        state = self.state(owner, touch=True)
        state.generation += 1
        task = self.tasks.get(owner)
        active = task is not None and not task.done()
        if active:
            task.cancel()
        state.phase = "ready"
        if clear:
            state.turns.clear()
            state.panels.clear()
            state.revision += 1
        return active

    async def stop(self) -> None:
        for owner in list(self.states):
            self.release(owner)
        if self.retiring:
            await asyncio.gather(*tuple(self.retiring), return_exceptions=True)
