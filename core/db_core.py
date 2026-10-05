"""Nucleo asincrono de persistencia para T.H.O.R.N, version Bastion 8."""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

import aiomysql
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

# El proceso comparte conexiones; cada sesion toma una prestada temporalmente.
_pool: aiomysql.Pool | None = None
_pool_lock = asyncio.Lock()
_env_path = Path(__file__).resolve().parents[1] / ".env"


def _obtener_configuracion_mysql() -> dict[str, Any]:
    """Lee la configuracion MySQL desde variables de entorno."""
    return {
        "host": os.getenv("MYSQL_HOST", "localhost"),
        "port": int(os.getenv("MYSQL_PORT", "3306")),
        "user": os.getenv("MYSQL_USER", "root"),
        "password": os.getenv("MYSQL_PASSWORD", ""),
        "db": os.getenv("MYSQL_DB", "db_thorn"),
        "charset": "utf8mb4",
        "minsize": 1,
        "maxsize": 10,
        "connect_timeout": 10,
        "autocommit": False,
    }


async def inicializar_motor_datos() -> aiomysql.Pool:
    """Inicializa y retorna el pool asincrono de conexiones MySQL."""
    global _pool

    async with _pool_lock:
        if _pool is not None and not _pool.closed:
            return _pool
        try:
            await asyncio.to_thread(load_dotenv, dotenv_path=_env_path)
            _pool = await aiomysql.create_pool(**_obtener_configuracion_mysql())
            logger.info("Pool MySQL inicializado correctamente.")
            return _pool
        except Exception:
            logger.critical("Fallo critico al inicializar el pool MySQL.", exc_info=True)
            raise


async def cerrar_motor_datos() -> None:
    """Cierra el pool MySQL de forma ordenada si esta activo."""
    global _pool

    async with _pool_lock:
        if _pool is None:
            return
        pool = _pool
        pool.close()
        try:
            async with asyncio.timeout(10):
                await pool.wait_closed()
        except (TimeoutError, asyncio.CancelledError):
            pool.terminate()
            await pool.wait_closed()
            raise
        finally:
            _pool = None
        logger.info("Pool MySQL cerrado correctamente.")


@asynccontextmanager
async def db_session() -> AsyncIterator[tuple[aiomysql.Connection, aiomysql.Cursor]]:
    """Entrega conexion y cursor; las escrituras requieren await conn.commit().

    Una transaccion no confirmada se revierte al salir. Ante errores o
    cancelacion se descarta la conexion para no reutilizar un protocolo parcial.
    """
    pool = _pool
    if pool is None or pool.closed:
        raise RuntimeError(
            "El pool MySQL no esta inicializado. "
            "Ejecuta inicializar_motor_datos() antes de abrir una sesion."
        )

    conn: aiomysql.Connection | None = None
    cur: aiomysql.Cursor | None = None

    try:
        conn = await pool.acquire()
        cur = await conn.cursor()
        yield conn, cur
    except BaseException as error:
        if conn is not None:
            conn.close()
        if isinstance(error, Exception):
            logger.exception("Error durante la sesion asincrona de base de datos.")
        raise
    finally:
        try:
            if conn is not None and not conn.closed:
                if cur is not None:
                    await cur.close()
                if conn.get_transaction_status():
                    await conn.rollback()
        except BaseException:
            if conn is not None:
                conn.close()
            raise
        finally:
            if conn is not None:
                pool.release(conn)


async def _test_conexion_mysql() -> None:
    """Ejecuta una prueba rapida consultando la version del servidor MySQL."""
    await inicializar_motor_datos()
    try:
        async with db_session() as (_, cur):
            await cur.execute("SELECT VERSION()")
            version = await cur.fetchone()
            print(f"Version MySQL: {version[0]}")
    finally:
        await cerrar_motor_datos()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    asyncio.run(_test_conexion_mysql())
