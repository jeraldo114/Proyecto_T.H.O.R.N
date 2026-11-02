"""Telemetria local de solo lectura; las llamadas al OS viven fuera del loop."""

from __future__ import annotations

import asyncio
import logging
import math
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .event_bus import EventBus, EventBusClosedError
from .events import EventPriority, EventType, ThornEvent
from .paths import data_root

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SystemSnapshot:
    """Muestra agregada, sin rutas, nombres de procesos ni usuarios."""

    sampled_at: str
    cpu_percent: float
    logical_cpus: int | None
    memory_total_bytes: int
    memory_available_bytes: int
    memory_percent: float
    disk_total_bytes: int
    disk_free_bytes: int
    disk_percent: float
    process_count: int


def read_system_snapshot() -> SystemSnapshot:
    """Consulta APIs nativas con psutil. Ejecutar exclusivamente en un hilo.

    La ventana CPU de 100 ms evita la primera lectura sin referencia y el
    estado por hilo de cpu_percent(interval=None). Solo mide el disco del proyecto.
    """
    import psutil

    cpu = psutil.cpu_percent(interval=0.1)
    memory = psutil.virtual_memory()
    disk = psutil.disk_usage(str(data_root()))
    return SystemSnapshot(
        sampled_at=datetime.now(timezone.utc).isoformat(),
        cpu_percent=cpu, logical_cpus=psutil.cpu_count(),
        memory_total_bytes=memory.total, memory_available_bytes=memory.available,
        memory_percent=memory.percent, disk_total_bytes=disk.total,
        disk_free_bytes=disk.free, disk_percent=disk.percent,
        process_count=len(psutil.pids()),
    )


class SystemSensor:
    """Mantiene una muestra en memoria y publica system.snapshot en el bus.

    No acumula lecturas concurrentes. Ante saturacion pierde el evento, no la
    muestra HTTP. Un fallo conserva la muestra previa, marcada como no vigente.
    """

    def __init__(
        self, bus: EventBus, *, interval: float = 2.0,
        reader: Callable[[], SystemSnapshot] = read_system_snapshot,
        publish_timeout: float = 0.25,
    ) -> None:
        if not math.isfinite(interval) or interval < 0.2:
            raise ValueError("El intervalo del sensor debe ser finito y >= 0.2 s.")
        if not math.isfinite(publish_timeout) or publish_timeout <= 0:
            raise ValueError("El plazo de publicacion debe ser positivo y finito.")
        self._bus = bus
        self._interval = interval
        self._reader = reader
        self._publish_timeout = publish_timeout
        self._snapshot: SystemSnapshot | None = None
        self._sample_time: float | None = None
        self._task: asyncio.Task[None] | None = None
        self._stop_task: asyncio.Task[None] | None = None
        self._last_failed = False
        self._samples = 0
        self._errors = 0
        self._dropped_events = 0

    @property
    def snapshot(self) -> SystemSnapshot | None:
        """Devuelve la ultima muestra inmutable sin consultar el OS."""
        return self._snapshot

    @property
    def stats(self) -> dict[str, Any]:
        """Describe vigencia, fallos y presion de cola mediante memoria local."""
        # NOTA DEL OPERADOR: la ultima lectura no siempre describe el presente.
        # La antiguedad se mide con reloj monotono, aunque ajustes la hora de Windows.
        age = None if self._sample_time is None else time.monotonic() - self._sample_time
        running = self._task is not None and not self._task.done() and self._stop_task is None
        return {
            "running": running, "interval_seconds": self._interval,
            "age_seconds": age,
            "fresh": running and not self._last_failed and age is not None
            and age <= max(5.0, self._interval * 3),
            "samples": self._samples, "errors": self._errors,
            "dropped_events": self._dropped_events,
        }

    async def start(self) -> None:
        """Programa el productor; el arranque HTTP no espera una lectura OS."""
        if self._stop_task is not None:
            raise RuntimeError("Crea otro sensor despues de cerrarlo.")
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="thorn.sensor.system")

    async def stop(self) -> None:
        """Cancela el productor y espera la lectura nativa que ya estuviese activa."""
        if self._stop_task is None:
            self._stop_task = asyncio.create_task(self._stop(), name="thorn.sensor.shutdown")
        try:
            await asyncio.shield(self._stop_task)
        except asyncio.CancelledError:
            await self._stop_task
            raise

    async def _stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)

    async def _collect(self) -> SystemSnapshot:
        # Cancelar to_thread no detiene el hilo: esperamos antes de liberar recursos.
        reading = asyncio.create_task(asyncio.to_thread(self._reader))
        try:
            return await asyncio.shield(reading)
        except asyncio.CancelledError:
            await asyncio.gather(reading, return_exceptions=True)
            raise

    async def _publish(self, event: ThornEvent) -> None:
        try:
            async with asyncio.timeout(self._publish_timeout):
                await self._bus.publish(event)
        except (TimeoutError, EventBusClosedError):
            self._dropped_events += 1

    async def _run(self) -> None:
        """Lee, publica y espera; los fallos de lectura se reintentan sin bucles rapidos."""
        while True:
            try:
                snapshot = await self._collect()
            except Exception as exc:
                self._last_failed = True
                self._errors += 1
                logger.warning("Fallo del sensor local (%s).", type(exc).__name__)
                await self._publish(ThornEvent(
                    EventType.MODULE_ERROR, "sensor.system",
                    {"module": "system_sensor", "error": "collection_failed"},
                ))
            else:
                self._snapshot = snapshot
                self._sample_time = time.monotonic()
                self._last_failed = False
                self._samples += 1
                await self._publish(ThornEvent(
                    EventType.SYSTEM_SNAPSHOT, "sensor.system", asdict(snapshot),
                    priority=EventPriority.LOW,
                ))
            await asyncio.sleep(self._interval)
