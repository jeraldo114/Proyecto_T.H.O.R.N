"""Ciclo de vida de T.H.O.R.N Bastion 9, con MySQL opcional para pruebas."""

from __future__ import annotations

import asyncio
import logging
import math
import time
from collections import deque
from collections.abc import Awaitable, Callable
from enum import StrEnum
from types import TracebackType
from typing import TYPE_CHECKING

from .event_bus import EventBus
from .events import EventPriority, EventType, ThornEvent
from .system_sensor import SystemSensor
from .sessions import SessionAuthority
from .version import RELEASE

logger = logging.getLogger(__name__)
if TYPE_CHECKING:
    from .services import AssistantServices
DatabaseStart = Callable[[], Awaitable[object]]
DatabaseClose = Callable[[], Awaitable[None]]


def _load_database_hooks() -> tuple[DatabaseStart, DatabaseClose]:
    """Carga dependencias de persistencia en el hilo de inicializacion."""
    from .db_core import cerrar_motor_datos, inicializar_motor_datos

    return inicializar_motor_datos, cerrar_motor_datos


class RuntimeState(StrEnum):
    """Estados observables de una instancia de ejecucion."""

    NEW = "new"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FAILED = "failed"


class ThornRuntime:
    """Gestiona un bus y el pool MySQL global en una ejecucion.

    Usar una instancia por proceso cuando MySQL esta habilitado. Los handlers
    solicitan el cierre con request_shutdown(); stop() pertenece al supervisor.
    """

    def __init__(
        self, *, use_database: bool = True, bus: EventBus | None = None,
        startup_timeout: float = 15.0, shutdown_timeout: float = 10.0,
        use_system_sensor: bool = False, sensor_interval: float = 2.0,
        enable_assistant_services: bool = False,
    ) -> None:
        for timeout in (startup_timeout, shutdown_timeout):
            if not math.isfinite(timeout) or timeout <= 0:
                raise ValueError("Los plazos deben ser positivos y finitos.")
        self.bus = bus if bus is not None else EventBus()
        self.sessions = SessionAuthority()
        self._started_at: float | None = None
        self._activity: deque[dict[str, str | int]] = deque(maxlen=120)
        for event_type in EventType:
            self.bus.subscribe(event_type, self._record_activity)
        self.system_sensor = (
            SystemSensor(self.bus, interval=sensor_interval) if use_system_sensor else None
        )
        self._state = RuntimeState.NEW
        self._use_database = use_database
        self._startup_timeout = startup_timeout
        self._shutdown_timeout = shutdown_timeout
        self._close_database: DatabaseClose | None = None
        self._lifecycle = asyncio.Lock()
        self._shutdown_requested = asyncio.Event()
        self._stop_task: asyncio.Task[None] | None = None
        self.services: AssistantServices | None = None
        self._enable_services = enable_assistant_services
        self._sensor_interval = sensor_interval

    @property
    def state(self) -> RuntimeState:
        """Devuelve el estado actual de la instancia."""
        return self._state

    def request_shutdown(self) -> None:
        """Solicita al supervisor el cierre sin esperar desde un handler."""
        self._shutdown_requested.set()

    @property
    def uptime_seconds(self) -> float:
        """Mide el tiempo desde el arranque con un reloj monotono."""
        return 0.0 if self._started_at is None else time.monotonic() - self._started_at

    @property
    def activity(self) -> list[dict[str, str | int]]:
        """Entrega copias de metadatos; no conserva payloads ni credenciales."""
        return [dict(item) for item in reversed(self._activity)]

    async def _record_activity(self, event: ThornEvent) -> None:
        self._activity.append({
            "event_id": event.event_id, "event_type": event.event_type,
            "source": event.source, "created_at": event.created_at.isoformat(),
            "priority": int(event.priority),
        })

    async def start(self) -> None:
        """Arranca persistencia y bus; revierte recursos si el arranque falla."""
        owns_startup = False
        try:
            async with self._lifecycle:
                if self._state is RuntimeState.RUNNING:
                    return
                if self._state is not RuntimeState.NEW:
                    raise RuntimeError("Crea un runtime nuevo para otra ejecucion.")
                owns_startup = True
                self._state = RuntimeState.STARTING
                # NOTA DEL OPERADOR: arrancamos por capas. Si algo falla,
                # stop() recoge tambien los recursos que ya estaban listos.
                async with asyncio.timeout(self._startup_timeout):
                    if self._use_database:
                        initialize, self._close_database = await asyncio.to_thread(
                            _load_database_hooks
                        )
                        await initialize()
                    if self._enable_services:
                        module = await asyncio.to_thread(__import__, "core.services", fromlist=["AssistantServices"])
                        self.services = await asyncio.to_thread(
                            module.AssistantServices, self,
                            database=self._use_database, sensor_interval=self._sensor_interval,
                        )
                        await self.services.start()
                    await self.bus.start()
                    if self.system_sensor is not None:
                        await self.system_sensor.start()
                    await self.bus.publish(ThornEvent(
                        EventType.RUNTIME_STARTED, "runtime",
                        {"name": "T.H.O.R.N", "version": RELEASE},
                        priority=EventPriority.HIGH,
                    ))
                    self._state = RuntimeState.RUNNING
                    self._started_at = time.monotonic()
                logger.info("T.H.O.R.N %s iniciado.", RELEASE)
        except BaseException:
            if owns_startup:
                self._state = RuntimeState.FAILED
                logger.exception("El arranque de T.H.O.R.N fallo.")
                await self.stop()
            raise

    async def stop(self) -> None:
        """Comparte un cierre protegido y espera su finalizacion al cancelar."""
        if self.bus.in_handler:
            raise RuntimeError("Usa request_shutdown() desde los manejadores.")
        # NOTA DEL OPERADOR: varios botones pueden pedir salir a la vez.
        # Todos esperan el mismo cierre; nadie desmonta el nucleo por su cuenta.
        if self._stop_task is None:
            self._stop_task = asyncio.create_task(self._stop(), name="thorn.shutdown")
        try:
            # shield permite terminar el cierre aunque se cancele el supervisor.
            await asyncio.shield(self._stop_task)
        except asyncio.CancelledError:
            await self._stop_task
            raise

    async def _stop(self) -> None:
        """Cierra consumidores antes de liberar las conexiones de persistencia."""
        async with self._lifecycle:
            failed = self._state is RuntimeState.FAILED
            self._state = RuntimeState.STOPPING
            self.sessions.close()
            self.request_shutdown()
            try:
                if self.system_sensor is not None:
                    await self.system_sensor.stop()
                if self.services is not None:
                    await self.services.stop()
                if self.bus.stats.accepting:
                    async with asyncio.timeout(self._shutdown_timeout):
                        await self.bus.publish(ThornEvent(
                            EventType.RUNTIME_STOPPING, "runtime",
                            priority=EventPriority.LOW,
                        ))
            except TimeoutError:
                logger.warning("No se pudo encolar el evento de cierre a tiempo.")
            except BaseException:
                failed = True
                logger.exception("Fallo al detener el productor local.")
                raise
            finally:
                try:
                    await self.bus.stop(timeout=self._shutdown_timeout)
                finally:
                    try:
                        if self._close_database is not None:
                            await self._close_database()
                    except BaseException:
                        self._state = RuntimeState.FAILED
                        logger.exception("Fallo al cerrar el motor de datos.")
                        raise
                    else:
                        self._state = RuntimeState.FAILED if failed else RuntimeState.STOPPED
                    finally:
                        self._close_database = None
            logger.info("T.H.O.R.N %s detenido.", RELEASE)

    async def run(self) -> None:
        """Ejecuta hasta request_shutdown() o cancelacion del supervisor."""
        try:
            await self.start()
            await self._shutdown_requested.wait()
        finally:
            await self.stop()

    async def __aenter__(self) -> ThornRuntime:
        await self.start()
        return self

    async def __aexit__(
        self, exc_type: type[BaseException] | None,
        exc_value: BaseException | None, traceback: TracebackType | None,
    ) -> None:
        await self.stop()
