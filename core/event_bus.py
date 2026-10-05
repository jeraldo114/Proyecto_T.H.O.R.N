"""Bus en memoria con contrapresion, prioridades y consumidores asincronos."""

from __future__ import annotations

import asyncio
import inspect
import logging
import math
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from itertools import count

from .events import ThornEvent

logger = logging.getLogger(__name__)
EventHandler = Callable[[ThornEvent], Awaitable[None]]


class EventBusClosedError(RuntimeError):
    """La publicacion se intento antes del arranque o durante el cierre."""


@dataclass(frozen=True, slots=True)
class BusStats:
    """Contadores acumulados; procesado no implica exito de los manejadores."""

    accepting: bool
    queued: int
    workers: int
    published: int
    processed: int
    handler_errors: int
    discarded: int


class EventBus:
    """Distribuye cada evento a sus suscriptores en un unico event loop.

    Los suscriptores de un evento se ejecutan en orden de registro. Diferentes
    eventos pueden procesarse en paralelo; con varios workers no se garantiza
    el orden de finalizacion. La entrega es en memoria, sin reintentos.
    """

    def __init__(
        self, *, max_queue_size: int = 1024, worker_count: int = 2,
        handler_timeout: float = 5.0,
    ) -> None:
        if max_queue_size < 1 or worker_count < 1:
            raise ValueError("La capacidad y el numero de workers deben ser positivos.")
        if not math.isfinite(handler_timeout) or handler_timeout <= 0:
            raise ValueError("El timeout debe ser positivo y finito.")
        self._queue: asyncio.PriorityQueue[tuple[int, int, ThornEvent]] = (
            asyncio.PriorityQueue(maxsize=max_queue_size)
        )
        self._worker_count = worker_count
        self._handler_timeout = handler_timeout
        self._subscribers: dict[str, list[EventHandler]] = {}
        self._workers: list[asyncio.Task[None]] = []
        # Desempata prioridades iguales sin comparar objetos ThornEvent.
        self._sequence = count()
        self._capacity = asyncio.Condition()
        self._lifecycle = asyncio.Lock()
        self._accepting = False
        self._published = 0
        self._processed = 0
        self._handler_errors = 0
        self._discarded = 0

    @property
    def in_handler(self) -> bool:
        """Indica si la tarea actual es uno de los consumidores del bus."""
        return asyncio.current_task() in self._workers

    @property
    def stats(self) -> BusStats:
        """Obtiene metricas sin ejecutar I/O."""
        return BusStats(
            self._accepting, self._queue.qsize(),
            sum(not worker.done() for worker in self._workers),
            self._published, self._processed, self._handler_errors, self._discarded,
        )

    def subscribe(self, event_type: str, handler: EventHandler) -> None:
        """Registra una funcion async una sola vez para el tema indicado."""
        if not event_type.strip():
            raise ValueError("El tema no puede estar vacio.")
        if not inspect.iscoroutinefunction(handler):
            raise TypeError("El manejador debe ser una funcion async.")
        subscribers = self._subscribers.setdefault(event_type, [])
        if handler not in subscribers:
            subscribers.append(handler)

    def unsubscribe(self, event_type: str, handler: EventHandler) -> bool:
        """Retira un suscriptor; el evento ya despachado conserva su copia."""
        subscribers = self._subscribers.get(event_type, [])
        if handler not in subscribers:
            return False
        subscribers.remove(handler)
        if not subscribers:
            self._subscribers.pop(event_type, None)
        return True

    async def start(self) -> None:
        """Arranca consumidores; llamadas repetidas no duplican workers."""
        async with self._lifecycle:
            if self._accepting:
                return
            async with self._capacity:
                self._workers = [
                    asyncio.create_task(self._consume(), name=f"thorn.bus.{index}")
                    for index in range(self._worker_count)
                ]
                self._accepting = True
                self._capacity.notify_all()

    async def publish(self, event: ThornEvent) -> None:
        """Espera espacio sin bloquear el hilo; retorna al aceptar el evento.

        Un manejador con la cola llena recibe QueueFull para evitar que todos
        los consumidores queden esperando espacio entre si. El cierre despierta
        a los productores externos pendientes con EventBusClosedError.
        """
        async with self._capacity:
            while self._accepting and self._queue.full():
                if self.in_handler:
                    raise asyncio.QueueFull("La cola esta llena durante un manejador.")
                # El productor cede el control: otros modulos siguen ejecutandose.
                await self._capacity.wait()
            if not self._accepting:
                raise EventBusClosedError("El bus no acepta eventos.")
            self._queue.put_nowait((int(event.priority), next(self._sequence), event))
            self._published += 1

    async def join(self) -> None:
        """Espera eventos aceptados; no puede invocarse desde un manejador."""
        if self.in_handler:
            raise RuntimeError("Un manejador no puede esperar su propio evento.")
        await self._queue.join()

    async def stop(self, *, timeout: float = 10.0) -> None:
        """Rechaza nuevas publicaciones, drena y cancela los consumidores.

        Si expira el plazo, descarta eventos pendientes y cancela manejadores
        activos. Los manejadores deben permitir la cancelacion cooperativa.
        """
        if self.in_handler:
            raise RuntimeError("Solicita el cierre desde el runtime, fuera del manejador.")
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("El plazo de cierre debe ser positivo y finito.")
        async with self._lifecycle:
            async with self._capacity:
                self._accepting = False
                self._capacity.notify_all()
            try:
                async with asyncio.timeout(timeout):
                    await self._queue.join()
            except TimeoutError:
                logger.warning("Se agoto el plazo para drenar el bus.")
            finally:
                for worker in self._workers:
                    worker.cancel()
                await asyncio.gather(*self._workers, return_exceptions=True)
                self._workers.clear()
                while not self._queue.empty():
                    self._queue.get_nowait()
                    self._queue.task_done()
                    self._discarded += 1

    async def _consume(self) -> None:
        """Despacha eventos y balancea task_done incluso ante cancelacion."""
        while True:
            _, _, event = await self._queue.get()
            try:
                async with self._capacity:
                    self._capacity.notify_all()
                for handler in tuple(self._subscribers.get(event.event_type, ())):
                    try:
                        async with asyncio.timeout(self._handler_timeout):
                            await handler(event)
                    except asyncio.CancelledError:
                        task = asyncio.current_task()
                        if task is not None and task.cancelling():
                            raise
                        self._handler_errors += 1
                        logger.warning("Manejador cancelado: evento=%s", event.event_id)
                    except Exception:
                        self._handler_errors += 1
                        logger.exception(
                            "Fallo de manejador: tema=%s evento=%s origen=%s",
                            event.event_type, event.event_id, event.source,
                        )
                self._processed += 1
            except asyncio.CancelledError:
                self._discarded += 1
                raise
            finally:
                self._queue.task_done()
