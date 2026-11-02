"""Nucleo asincrono de persistencia para T.H.O.R.N, version Bastion 9."""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

import aiomysql
from dotenv import load_dotenv
from .paths import data_root

logger = logging.getLogger(__name__)

# El proceso comparte conexiones; cada sesion toma una prestada temporalmente.
_pool: aiomysql.Pool | None = None
_pool_lock = asyncio.Lock()
_env_path = data_root() / ".env"


def _load_environment(allow_pending: bool = False) -> None:
    """El acceso a archivos y DPAPI ocurre antes de abrir el pool, en un worker."""
    from thorn_web.config import protected_environment
    provided = set(os.environ)
    load_dotenv(dotenv_path=_env_path)
    protected_environment(_env_path.parent, provided, allow_pending=allow_pending)


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


async def inicializar_motor_datos(*, allow_pending: bool = False) -> aiomysql.Pool:
    """Inicializa y retorna el pool asincrono de conexiones MySQL."""
    global _pool

    # NOTA DEL OPERADOR: si dos modulos arrancan juntos, este candado evita
    # crear dos pools. Diez conexiones compartidas, cada una con su turno.
    async with _pool_lock:
        if _pool is not None and not _pool.closed:
            return _pool
        try:
            await asyncio.to_thread(_load_environment, allow_pending)
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
        # NOTA DEL OPERADOR: prestamos la conexion, no decidimos tu transaccion.
        # Quien escribe confirma con await conn.commit(); al salir se revierte
        # lo pendiente para que la siguiente sesion no herede trabajo ajeno.
        yield conn, cur
    except BaseException as error:
        # Una cancelacion puede dejar una respuesta MySQL a medio leer.
        # Esa conexion se descarta: devolverla sana vale mas que reutilizarla.
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
