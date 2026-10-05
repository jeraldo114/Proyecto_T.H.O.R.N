"""Escritura de logs en un hilo de apoyo, compartida por ambas entradas."""

from __future__ import annotations

import logging
import queue
from collections.abc import Iterator
from contextlib import contextmanager
from logging.handlers import QueueHandler, QueueListener


@contextmanager
def queued_logging(*, level: int = logging.INFO) -> Iterator[None]:
    """Encola mensajes y restaura el logger al salir del proceso supervisor.

    Entrar y salir de este contexto fuera de asyncio.run(): el listener debe
    terminar de escribir despues de cerrar el runtime y el servidor.
    """
    records: queue.SimpleQueue[logging.LogRecord] = queue.SimpleQueue()
    output = logging.StreamHandler()
    output.setFormatter(logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
    ))
    queued = QueueHandler(records)
    root = logging.getLogger()
    previous_handlers, previous_level = root.handlers[:], root.level
    root.handlers = [queued]
    root.setLevel(level)
    listener = QueueListener(records, output, respect_handler_level=True)
    listener.start()
    try:
        yield
    finally:
        listener.stop()
        root.handlers = previous_handlers
        root.setLevel(previous_level)
        queued.close()
        output.close()
