"""Pruebas del supervisor con persistencia simulada."""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from core.events import EventType, ThornEvent
from core.runtime import RuntimeState, ThornRuntime


class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_activity_is_bounded_copied_and_excludes_payloads(self) -> None:
        runtime = ThornRuntime(use_database=False)
        async with runtime:
            for _ in range(130):
                await runtime.bus.publish(ThornEvent(
                    EventType.COMMAND_RECEIVED, "test", {"private": "not-for-hud"},
                ))
            await runtime.bus.join()
            self.assertEqual(len(runtime.activity), 120)
            self.assertNotIn("not-for-hud", str(runtime.activity))
            copy = runtime.activity
            copy[0]["source"] = "changed"
            self.assertEqual(runtime.activity[0]["source"], "test")
            # En Windows este turno puede terminar dentro del mismo tick del reloj.
            self.assertEqual(runtime.state, RuntimeState.RUNNING)
            self.assertGreaterEqual(runtime.uptime_seconds, 0)

    async def test_sensor_stops_before_shutdown_event_and_database(self) -> None:
        order: list[str] = []
        runtime = ThornRuntime(use_system_sensor=True)
        initialize = AsyncMock()

        async def stop_sensor() -> None:
            order.append("sensor.stopped")

        async def handler(event: ThornEvent) -> None:
            order.append("bus.stopping")

        async def close() -> None:
            order.append("db.closed")

        runtime.bus.subscribe(EventType.RUNTIME_STOPPING, handler)
        with (
            patch("core.runtime._load_database_hooks", return_value=(initialize, close)),
            patch.object(runtime.system_sensor, "start", new=AsyncMock()),
            patch.object(runtime.system_sensor, "stop", new=stop_sensor),
        ):
            async with runtime:
                pass
        self.assertEqual(order, ["sensor.stopped", "bus.stopping", "db.closed"])

    async def test_sensor_start_failure_closes_every_resource(self) -> None:
        runtime = ThornRuntime(use_system_sensor=True)
        initialize, close, stop_sensor = AsyncMock(), AsyncMock(), AsyncMock()
        with (
            patch("core.runtime._load_database_hooks", return_value=(initialize, close)),
            patch.object(runtime.system_sensor, "start", new=AsyncMock(side_effect=OSError("expected"))),
            patch.object(runtime.system_sensor, "stop", new=stop_sensor),
            self.assertLogs("core.runtime", level="ERROR"),
        ):
            with self.assertRaises(OSError):
                await runtime.start()
        stop_sensor.assert_awaited_once()
        close.assert_awaited_once()
        self.assertEqual(runtime.state, RuntimeState.FAILED)
        self.assertEqual(runtime.bus.stats.workers, 0)

    async def test_context_emits_lifecycle_and_closes(self) -> None:
        runtime = ThornRuntime(use_database=False)
        seen: list[str] = []

        async def handler(event: ThornEvent) -> None:
            seen.append(event.event_type)

        runtime.bus.subscribe(EventType.RUNTIME_STARTED, handler)
        runtime.bus.subscribe(EventType.RUNTIME_STOPPING, handler)
        async with runtime:
            self.assertEqual(runtime.state, RuntimeState.RUNNING)
            await runtime.start()
        self.assertEqual(seen, [EventType.RUNTIME_STARTED, EventType.RUNTIME_STOPPING])
        self.assertEqual(runtime.state, RuntimeState.STOPPED)
        self.assertEqual(runtime.bus.stats.workers, 0)
        await runtime.stop()

    async def test_handler_requests_shutdown(self) -> None:
        runtime = ThornRuntime(use_database=False)

        async def handler(event: ThornEvent) -> None:
            with self.assertRaises(RuntimeError):
                await runtime.stop()
            runtime.request_shutdown()

        runtime.bus.subscribe(EventType.RUNTIME_STARTED, handler)
        await asyncio.wait_for(runtime.run(), 1)
        self.assertEqual(runtime.state, RuntimeState.STOPPED)

    async def test_failed_database_start_cleans_up_without_deadlock(self) -> None:
        initialize = AsyncMock(side_effect=ConnectionError("expected"))
        close = AsyncMock()
        runtime = ThornRuntime()
        with patch("core.runtime._load_database_hooks", return_value=(initialize, close)):
            with self.assertLogs("core.runtime", level="ERROR"):
                with self.assertRaises(ConnectionError):
                    await asyncio.wait_for(runtime.start(), 1)
        close.assert_awaited_once()
        self.assertEqual(runtime.state, RuntimeState.FAILED)
        self.assertEqual(runtime.bus.stats.workers, 0)

    async def test_database_closes_after_handlers_finish(self) -> None:
        initialize = AsyncMock()
        order: list[str] = []

        async def close() -> None:
            order.append("db.closed")

        async def handler(event: ThornEvent) -> None:
            await asyncio.sleep(0)
            order.append(event.event_type)

        runtime = ThornRuntime()
        runtime.bus.subscribe(EventType.RUNTIME_STOPPING, handler)
        with patch("core.runtime._load_database_hooks", return_value=(initialize, close)):
            await runtime.start()
            await asyncio.gather(runtime.stop(), runtime.stop())
        self.assertEqual(order, [EventType.RUNTIME_STOPPING, "db.closed"])
        initialize.assert_awaited_once()

    async def test_cancelled_supervisor_waits_for_database_cleanup(self) -> None:
        initialize = AsyncMock()
        entered = asyncio.Event()
        released = asyncio.Event()

        async def close() -> None:
            entered.set()
            await released.wait()

        runtime = ThornRuntime()
        with patch("core.runtime._load_database_hooks", return_value=(initialize, close)):
            await runtime.start()
            stopping = asyncio.create_task(runtime.stop())
            try:
                await asyncio.wait_for(entered.wait(), 1)
                stopping.cancel()
                await asyncio.sleep(0)
                self.assertFalse(stopping.done())
                released.set()
                with self.assertRaises(asyncio.CancelledError):
                    await asyncio.wait_for(stopping, 1)
                self.assertEqual(runtime.state, RuntimeState.STOPPED)
            finally:
                released.set()
                await asyncio.gather(stopping, return_exceptions=True)

    async def test_startup_timeout_closes_database(self) -> None:
        async def initialize() -> None:
            await asyncio.Event().wait()

        close = AsyncMock()
        runtime = ThornRuntime(startup_timeout=0.01)
        with patch(
            "core.runtime.asyncio.to_thread",
            new=AsyncMock(return_value=(initialize, close)),
        ):
            with self.assertLogs("core.runtime", level="ERROR"):
                with self.assertRaises(TimeoutError):
                    await asyncio.wait_for(runtime.start(), 1)
        close.assert_awaited_once()
        self.assertEqual(runtime.state, RuntimeState.FAILED)

    async def test_cancelled_run_stops_workers(self) -> None:
        runtime = ThornRuntime(use_database=False)
        started = asyncio.Event()

        async def handler(event: ThornEvent) -> None:
            started.set()

        runtime.bus.subscribe(EventType.RUNTIME_STARTED, handler)
        task = asyncio.create_task(runtime.run())
        try:
            await asyncio.wait_for(started.wait(), 1)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(task, 1)
            self.assertEqual(runtime.state, RuntimeState.STOPPED)
            self.assertEqual(runtime.bus.stats.workers, 0)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def test_database_close_failure_sets_failed_state(self) -> None:
        initialize = AsyncMock()
        close = AsyncMock(side_effect=ConnectionError("expected"))
        runtime = ThornRuntime()
        with patch("core.runtime._load_database_hooks", return_value=(initialize, close)):
            await runtime.start()
            with self.assertLogs("core.runtime", level="ERROR"):
                with self.assertRaises(ConnectionError):
                    await runtime.stop()
        self.assertEqual(runtime.state, RuntimeState.FAILED)
        self.assertEqual(runtime.bus.stats.workers, 0)
