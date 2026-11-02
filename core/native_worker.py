"""Puente para librerias nativas: I/O, inferencia y destruccion fuera del loop."""

from __future__ import annotations

import asyncio
from typing import Any, Callable


async def drain_native(task: asyncio.Task[None]) -> None:
    """Cancelar una coroutine no detiene su hilo; espera incluso cancelaciones repetidas."""
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            continue
        except Exception:
            break
    if not task.cancelled():
        task.exception()


async def run_native(function: Callable[..., Any], *args: Any) -> Any:
    """Libera tambien un resultado abandonado en el worker, nunca en HTTP."""
    # NOTA DEL OPERADOR: cancelar el await no apaga una DLL que ya esta trabajando.
    # Esperamos al hilo y liberamos su resultado alli antes de cerrar el servicio.
    results: list[Any] = []
    def invoke() -> None:
        results.append(function(*args))
    task = asyncio.create_task(asyncio.to_thread(invoke))
    try:
        await asyncio.shield(task)
        return results.pop()
    except asyncio.CancelledError:
        await drain_native(task)
        await drain_native(asyncio.create_task(asyncio.to_thread(results.clear)))
        raise
