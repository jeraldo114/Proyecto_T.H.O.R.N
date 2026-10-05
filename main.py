"""Entrada local de T.H.O.R.N Bastion 8. Requiere Python 3.11 o superior."""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal

from core.events import EventType, ThornEvent
from core.logging_config import queued_logging
from core.runtime import ThornRuntime

logger = logging.getLogger("thorn")


async def _main(*, demo: bool, use_database: bool) -> None:
    """Supervisa el runtime; la demo valida el flujo sin ejecutar comandos OS."""
    runtime = ThornRuntime(use_database=use_database)

    async def on_event(event: ThornEvent) -> None:
        logger.info("Evento recibido: %s | origen=%s", event.event_type, event.source)

    for event_type in EventType:
        runtime.bus.subscribe(event_type, on_event)

    loop = asyncio.get_running_loop()
    installed_signals: list[signal.Signals] = []
    for signum in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signum, runtime.request_shutdown)
        except (NotImplementedError, RuntimeError):
            # asyncio.run atiende Ctrl+C en Windows, donde esta API no existe.
            continue
        installed_signals.append(signum)
    try:
        if demo:
            async with runtime:
                await runtime.bus.publish(ThornEvent(
                    EventType.COMMAND_RECEIVED, "demo", {"command": "status"},
                ))
                await runtime.bus.join()
        else:
            await runtime.run()
    finally:
        for signum in installed_signals:
            loop.remove_signal_handler(signum)
        logger.info("Metricas finales: %s", runtime.bus.stats)


def main() -> int:
    """Configura logs fuera del loop y ejecuta el supervisor asincrono."""
    parser = argparse.ArgumentParser(description="T.H.O.R.N - Bastion 8")
    parser.add_argument("--demo", action="store_true", help="Procesa un evento y termina.")
    parser.add_argument(
        "--sin-db", action="store_true", help="Ejecuta el bus sin conectar a MySQL.",
    )
    args = parser.parse_args()

    with queued_logging():
        try:
            asyncio.run(_main(demo=args.demo, use_database=not args.sin_db))
            return 0
        except KeyboardInterrupt:
            logger.info("Cierre solicitado desde la consola.")
            return 0
        except Exception:
            logger.exception("T.H.O.R.N termino por un error.")
            return 1


if __name__ == "__main__":
    raise SystemExit(main())
