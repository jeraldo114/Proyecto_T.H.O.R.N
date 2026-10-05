"""Servidor HTTP local de T.H.O.R.N con Django ASGI y Uvicorn."""

from __future__ import annotations

import argparse
import asyncio
import importlib
import logging
import os
from typing import TYPE_CHECKING

import uvicorn

from core.logging_config import queued_logging

if TYPE_CHECKING:
    from thorn_web.lifespan import ThornASGI

logger = logging.getLogger("thorn.server")


def _load_application(*, without_database: bool) -> ThornASGI:
    """Prepara archivos e imports en un hilo, antes de aceptar peticiones."""
    from thorn_web.config import prepare_environment

    prepare_environment()
    if without_database:
        os.environ["THORN_USE_DATABASE"] = "0"
    return importlib.import_module("thorn_web.asgi").application


async def _serve(*, port: int, without_database: bool) -> None:
    """Ejecuta un solo servidor y un solo runtime en el mismo event loop."""
    application = await asyncio.to_thread(
        _load_application, without_database=without_database,
    )
    config = uvicorn.Config(
        app=application, host="127.0.0.1", port=port, lifespan="on",
        workers=1, loop="asyncio", log_config=None, access_log=False,
        limit_concurrency=64, timeout_graceful_shutdown=15,
    )
    server = uvicorn.Server(config)
    await server.serve()
    if not server.started:
        raise RuntimeError("El servidor no pudo completar el arranque.")


def main() -> int:
    """Entrada de consola; escribe los logs desde un hilo dedicado."""
    parser = argparse.ArgumentParser(description="API local de T.H.O.R.N Bastion 8")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--sin-db", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("El puerto debe estar entre 1 y 65535.")
    with queued_logging():
        try:
            asyncio.run(_serve(port=args.port, without_database=args.sin_db))
            return 0
        except KeyboardInterrupt:
            logger.info("Servidor detenido desde la consola.")
            return 0
        except Exception:
            logger.exception("Fallo del servidor local.")
            return 1


if __name__ == "__main__":
    raise SystemExit(main())
