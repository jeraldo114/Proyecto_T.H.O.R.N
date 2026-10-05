"""Pruebas de recursos MySQL con dobles; no conectan a un servidor."""

from __future__ import annotations

import asyncio
import importlib.util
import unittest
from unittest.mock import AsyncMock, Mock, patch

HAS_DEPENDENCIES = all(
    importlib.util.find_spec(name) is not None for name in ("aiomysql", "dotenv")
)
if HAS_DEPENDENCIES:
    from core import db_core


@unittest.skipUnless(HAS_DEPENDENCIES, "Instala aiomysql y python-dotenv.")
class DatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        db_core._pool = None
        db_core._pool_lock = asyncio.Lock()
        self.cursor = Mock(close=AsyncMock())
        self.conn = Mock(
            closed=False, cursor=AsyncMock(return_value=self.cursor),
            rollback=AsyncMock(), get_transaction_status=Mock(return_value=False),
        )
        self.conn.close.side_effect = lambda: setattr(self.conn, "closed", True)
        self.pool = Mock(
            closed=False, acquire=AsyncMock(return_value=self.conn),
            wait_closed=AsyncMock(),
        )

    async def asyncTearDown(self) -> None:
        db_core._pool = None

    async def test_concurrent_initialization_creates_only_one_pool(self) -> None:
        async def create_pool(**kwargs: object) -> Mock:
            self.assertEqual(kwargs["minsize"], 1)
            self.assertEqual(kwargs["maxsize"], 10)
            await asyncio.sleep(0)
            return self.pool

        with patch("core.db_core.load_dotenv"), patch(
            "core.db_core.aiomysql.create_pool", new=AsyncMock(side_effect=create_pool),
        ) as create:
            pools = await asyncio.gather(*(
                db_core.inicializar_motor_datos() for _ in range(4)
            ))
        self.assertTrue(all(pool is self.pool for pool in pools))
        create.assert_awaited_once()
        await db_core.cerrar_motor_datos()
        self.pool.close.assert_called_once()
        self.pool.wait_closed.assert_awaited_once()

    async def test_failed_cursor_close_still_releases_connection(self) -> None:
        db_core._pool = self.pool
        self.cursor.close.side_effect = RuntimeError("expected")
        with self.assertRaises(RuntimeError):
            async with db_core.db_session():
                pass
        self.conn.close.assert_called_once()
        self.pool.release.assert_called_once_with(self.conn)

    async def test_uncommitted_transaction_is_rolled_back(self) -> None:
        db_core._pool = self.pool
        self.conn.get_transaction_status.return_value = True
        async with db_core.db_session() as (conn, cursor):
            self.assertIs(conn, self.conn)
            self.assertIs(cursor, self.cursor)
        self.cursor.close.assert_awaited_once()
        self.conn.rollback.assert_awaited_once()
        self.pool.release.assert_called_once_with(self.conn)

    async def test_cancelled_query_discards_and_releases_connection(self) -> None:
        db_core._pool = self.pool
        entered = asyncio.Event()

        async def query() -> None:
            async with db_core.db_session():
                entered.set()
                await asyncio.Event().wait()

        task = asyncio.create_task(query())
        await asyncio.wait_for(entered.wait(), 1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.conn.close.assert_called_once()
        self.pool.release.assert_called_once_with(self.conn)

    async def test_connection_failure_does_not_leave_a_pool(self) -> None:
        with patch("core.db_core.load_dotenv"), patch(
            "core.db_core.aiomysql.create_pool",
            new=AsyncMock(side_effect=ConnectionError("expected")),
        ), self.assertLogs("core.db_core", level="CRITICAL"):
            with self.assertRaises(ConnectionError):
                await db_core.inicializar_motor_datos()
        self.assertIsNone(db_core._pool)

    async def test_shutdown_terminates_pool_after_timeout(self) -> None:
        db_core._pool = self.pool
        self.pool.wait_closed.side_effect = [TimeoutError(), None]
        with self.assertRaises(TimeoutError):
            await db_core.cerrar_motor_datos()
        self.pool.terminate.assert_called_once()
        self.assertIsNone(db_core._pool)
