"""Ciclo de vida de T.H.O.R.N Bastion 8, con MySQL opcional para pruebas."""

from __future__ import annotations

import asyncio
import logging
import math
from collections.abc import Awaitable, Callable
from enum import StrEnum
from types import TracebackType

from .event_bus import EventBus
from .events import EventPriority, EventType, ThornEvent

logger = logging.getLogger(__name__)
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
    ) -> None:
        for timeout in (startup_timeout, shutdown_timeout):
            if not math.isfinite(timeout) or timeout <= 0:
                raise ValueError("Los plazos deben ser positivos y finitos.")
        self.bus = bus if bus is not None else EventBus()
        self._state = RuntimeState.NEW
        self._use_database = use_database
        self._startup_timeout = startup_timeout
        self._shutdown_timeout = shutdown_timeout
        self._close_database: DatabaseClose | None = None
        self._lifecycle = asyncio.Lock()
        self._shutdown_requested = asyncio.Event()
        self._stop_task: asyncio.Task[None] | None = None

    @property
    def state(self) -> RuntimeState:
        """Devuelve el estado actual de la instancia."""
        return self._state

    def request_shutdown(self) -> None:
        """Solicita al supervisor el cierre sin esperar desde un handler."""
        self._shutdown_requested.set()

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
                async with asyncio.timeout(self._startup_timeout):
                    if self._use_database:
                        initialize, self._close_database = await asyncio.to_thread(
                            _load_database_hooks
                        )
                        await initialize()
                    await self.bus.start()
                    await self.bus.publish(ThornEvent(
                        EventType.RUNTIME_STARTED, "runtime",
                        {"name": "T.H.O.R.N", "version": "Bastion 8"},
                        priority=EventPriority.HIGH,
                    ))
                    self._state = RuntimeState.RUNNING
                logger.info("T.H.O.R.N Bastion 8 iniciado.")
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
            self.request_shutdown()
            try:
                if self.bus.stats.accepting:
                    async with asyncio.timeout(self._shutdown_timeout):
                        await self.bus.publish(ThornEvent(
                            EventType.RUNTIME_STOPPING, "runtime",
                            priority=EventPriority.LOW,
                        ))
            except TimeoutError:
                logger.warning("No se pudo encolar el evento de cierre a tiempo.")
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
            logger.info("T.H.O.R.N Bastion 8 detenido.")

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
