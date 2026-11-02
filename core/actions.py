"""Acciones Windows cerradas: preparacion, confirmacion y auditoria previa."""

from __future__ import annotations

import asyncio
import ctypes
import os
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .audit import AuditStore
from .paths import data_root

# NOTA DEL OPERADOR: este es el limite real de lo que T.H.O.R.N puede abrir.
# Para ampliarlo, agrega un destino fijo y revisa su confirmacion y auditoria.
# El texto del modelo nunca se convierte en una linea de comandos.
CATALOG = {
    "open_notepad": "Abrir Bloc de notas",
    "open_calculator": "Abrir Calculadora",
    "open_project": "Abrir carpeta de T.H.O.R.N",
    "media_play_pause": "Alternar reproducir/pausar multimedia (Windows)",
    "media_stop": "Detener multimedia (Windows)",
}


def _media_key(virtual_key: int) -> None:
    """Envia una tecla multimedia conocida; no acepta texto ni atajos arbitrarios."""
    from ctypes import wintypes
    class Keyboard(ctypes.Structure):
        _fields_ = [("vk", wintypes.WORD), ("scan", wintypes.WORD), ("flags", wintypes.DWORD),
                    ("time", wintypes.DWORD), ("extra", wintypes.WPARAM)]
    class Mouse(ctypes.Structure):
        _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG), ("data", wintypes.DWORD),
                    ("flags", wintypes.DWORD), ("time", wintypes.DWORD), ("extra", wintypes.WPARAM)]
    class Payload(ctypes.Union):
        _fields_ = [("keyboard", Keyboard), ("mouse", Mouse)]
    class Input(ctypes.Structure):
        _anonymous_ = ("payload",)
        _fields_ = [("kind", wintypes.DWORD), ("payload", Payload)]
    values = (Input * 2)()
    for item, flags in zip(values, (0, 2)):
        item.kind = 1
        item.keyboard = Keyboard(virtual_key, 0, flags, 0, 0)
    send = ctypes.windll.user32.SendInput
    send.argtypes = (wintypes.UINT, ctypes.POINTER(Input), ctypes.c_int)
    send.restype = wintypes.UINT
    if send(2, values, ctypes.sizeof(Input)) != 2:
        raise OSError("media_key_failed")


def windows_actions_allowed() -> bool:
    """Las aperturas no deben heredar un token elevado accidentalmente."""
    if os.name != "nt":
        return False
    try:
        return not bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (OSError, AttributeError):
        return False


def open_windows_target(action_id: str) -> None:
    """Resuelve rutas del sistema sin PATH, cmd, PowerShell ni argumentos libres."""
    if os.name != "nt" or action_id not in CATALOG:
        raise RuntimeError("action_unavailable")
    if action_id in {"media_play_pause", "media_stop"}:
        _media_key(0xB3 if action_id == "media_play_pause" else 0xB2)
        return
    if action_id == "open_project":
        target = data_root()
    else:
        buffer = ctypes.create_unicode_buffer(32768)
        size = ctypes.windll.kernel32.GetWindowsDirectoryW(buffer, len(buffer))
        if not 0 < size < len(buffer):
            raise RuntimeError("windows_directory_unavailable")
        name = "notepad.exe" if action_id == "open_notepad" else "calc.exe"
        target = Path(buffer.value) / "System32" / name
        if not target.is_file() or target.is_symlink():
            raise RuntimeError("target_unavailable")
    os.startfile(str(target), "open")


@dataclass(frozen=True, slots=True)
class PendingAction:
    action_id: str
    expires_at: float


class ActionController:
    """Las propuestas no ejecutan nada. Una confirmacion caduca y se consume una vez."""

    def __init__(self, audit: AuditStore, *, executor: Callable[[str], None] = open_windows_target,
                 supported: bool | None = None, clock: Callable[[], float] = time.monotonic) -> None:
        self.audit, self.executor, self.clock = audit, executor, clock
        self.supported = windows_actions_allowed() if supported is None else supported
        self._pending: dict[str, PendingAction] = {}
        self._lock = asyncio.Lock()
        self._stopping = False

    @property
    def enabled(self) -> bool:
        return self.supported and self.audit.enabled and not self._stopping

    def catalog(self) -> list[dict[str, str]]:
        return [{"id": key, "label": label} for key, label in CATALOG.items()] if self.enabled else []

    def prepare(self, action_id: str) -> dict[str, object]:
        if not self.enabled:
            raise RuntimeError("actions_require_windows_and_database")
        if action_id not in CATALOG:
            raise ValueError("action_not_allowed")
        now = self.clock()
        self._pending = {key: value for key, value in self._pending.items() if value.expires_at > now}
        if len(self._pending) >= 20:
            raise RuntimeError("too_many_pending_actions")
        confirmation = secrets.token_urlsafe(32)
        self._pending[confirmation] = PendingAction(action_id, now + 60)
        return {"confirmation_id": confirmation, "action_id": action_id,
                "label": CATALOG[action_id], "expires_in_seconds": 60}

    async def confirm(self, confirmation: str) -> dict[str, object]:
        if self._lock.locked():
            raise RuntimeError("action_busy")
        async with self._lock:
            # La confirmacion se consume antes de ejecutar. Un doble clic no
            # debe abrir dos aplicaciones con el mismo permiso.
            pending = self._pending.pop(confirmation, None)
            if not self.enabled or pending is None or pending.expires_at <= self.clock():
                raise ValueError("confirmation_expired_or_consumed")
            # El registro durable es obligatorio antes de abrir cualquier aplicacion.
            record_id = await self.audit.append("os.action", "executing", CATALOG[pending.action_id],
                                                {"action_id": pending.action_id})
            if record_id is None:
                raise RuntimeError("audit_required")
            worker = asyncio.create_task(asyncio.to_thread(self.executor, pending.action_id))
            cancelled = False
            try:
                try:
                    await asyncio.shield(worker)
                except asyncio.CancelledError:
                    cancelled = True
                    await worker
            except Exception:
                await self.audit.finish(record_id, "failed")
                raise RuntimeError("action_failed") from None
            # ShellExecute aceptado no prueba que la ventana permanezca abierta.
            try:
                await self.audit.finish(record_id, "launched")
            except Exception:
                raise RuntimeError("action_outcome_unrecorded") from None
            if cancelled:
                raise asyncio.CancelledError
            return {"action_id": pending.action_id, "status": "launched", "audit_id": record_id}

    async def stop(self) -> None:
        self._stopping = True
        self._pending.clear()
        async with self._lock:
            pass
