"""Verifica telemetria sin usar las metricas de la maquina de pruebas."""

from __future__ import annotations

import asyncio
import threading
import unittest
from collections.abc import Callable
from unittest.mock import Mock, patch

from core.event_bus import EventBus
from core.events import EventType, ThornEvent
from core.system_sensor import SystemSensor, SystemSnapshot, read_system_snapshot


def sample() -> SystemSnapshot:
    return SystemSnapshot("2026-01-01T00:00:00+00:00", 12.0, 8, 1000, 700, 30.0, 2000, 1000, 50.0, 42)


async def eventually(condition: Callable[[], bool]) -> None:
    async with asyncio.timeout(2):
        while not condition():
            await asyncio.sleep(0.005)


class SensorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.bus = EventBus()
        await self.bus.start()
        self.sensors: list[SystemSensor] = []

    async def asyncTearDown(self) -> None:
        for sensor in self.sensors:
            await sensor.stop()
        await self.bus.stop()

    def make_sensor(self, **kwargs: object) -> SystemSensor:
        sensor = SystemSensor(self.bus, **kwargs)
        self.sensors.append(sensor)
        return sensor

    async def test_reader_runs_outside_loop_and_publishes_snapshot(self) -> None:
        loop_thread = threading.get_ident()
        reader_thread: list[int] = []
        seen: list[ThornEvent] = []

        def read() -> SystemSnapshot:
            reader_thread.append(threading.get_ident())
            return sample()

        async def handler(event: ThornEvent) -> None:
            seen.append(event)

        self.bus.subscribe(EventType.SYSTEM_SNAPSHOT, handler)
        sensor = self.make_sensor(reader=read)
        await sensor.start()
        await sensor.start()
        await eventually(lambda: bool(seen))
        self.assertNotEqual(reader_thread[0], loop_thread)
        self.assertEqual(sensor.snapshot, sample())
        self.assertTrue(sensor.stats["fresh"])
        self.assertEqual(seen[0].payload["process_count"], 42)
        self.assertEqual(seen[0].source, "sensor.system")
        await sensor.stop()
        self.assertFalse(sensor.stats["fresh"])
        with self.assertRaises(RuntimeError):
            await sensor.start()

    async def test_failure_is_observable_and_recovers(self) -> None:
        reader = Mock(side_effect=[PermissionError("private details"), sample()])
        errors: list[ThornEvent] = []

        async def handler(event: ThornEvent) -> None:
            errors.append(event)

        self.bus.subscribe(EventType.MODULE_ERROR, handler)
        sensor = self.make_sensor(reader=reader, interval=0.2)
        with self.assertLogs("core.system_sensor", level="WARNING") as logs:
            await sensor.start()
            await eventually(lambda: bool(errors))
        self.assertFalse(sensor.stats["fresh"])
        self.assertNotIn("private details", str(logs.output) + str(errors[0].payload))
        await eventually(lambda: sensor.stats["samples"] == 1)
        self.assertTrue(sensor.stats["fresh"])
        self.assertEqual(sensor.stats["errors"], 1)

    async def test_error_marks_previous_sample_not_fresh(self) -> None:
        reader = Mock(side_effect=[sample(), OSError("expected")])
        sensor = self.make_sensor(reader=reader, interval=0.2)
        await sensor.start()
        await eventually(lambda: sensor.stats["samples"] == 1)
        with self.assertLogs("core.system_sensor", level="WARNING"):
            await eventually(lambda: sensor.stats["errors"] == 1)
        self.assertEqual(sensor.snapshot, sample())
        self.assertFalse(sensor.stats["fresh"])

    async def test_old_sample_is_not_fresh(self) -> None:
        sensor = self.make_sensor(reader=sample)
        await sensor.start()
        await eventually(lambda: sensor.snapshot is not None)
        with patch("core.system_sensor.time.monotonic", return_value=sensor._sample_time + 7):
            self.assertFalse(sensor.stats["fresh"])

    async def test_stop_waits_for_active_thread_even_when_caller_cancelled(self) -> None:
        entered = threading.Event()
        release = threading.Event()
        finished = threading.Event()

        def read() -> SystemSnapshot:
            entered.set()
            if not release.wait(3):
                raise TimeoutError("test reader")
            finished.set()
            return sample()

        sensor = self.make_sensor(reader=read)
        await sensor.start()
        stopping: asyncio.Task[None] | None = None
        try:
            await eventually(entered.is_set)
            stopping = asyncio.create_task(sensor.stop())
            await asyncio.sleep(0)
            stopping.cancel()
            await asyncio.sleep(0)
            self.assertFalse(stopping.done())
            # La espera nativa no impide que esta tarea del loop avance.
            self.assertFalse(finished.is_set())
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(stopping, 2)
            self.assertTrue(finished.is_set())
            self.assertEqual(sensor.stats["samples"], 0)
        finally:
            release.set()
            if stopping is not None:
                await asyncio.gather(stopping, return_exceptions=True)

    async def test_full_bus_drops_event_but_keeps_sample(self) -> None:
        bus = EventBus(max_queue_size=1, worker_count=1)
        await bus.start()
        entered = asyncio.Event()
        release = asyncio.Event()

        async def blocked(event: ThornEvent) -> None:
            entered.set()
            await release.wait()

        bus.subscribe("blocked", blocked)
        sensor = SystemSensor(bus, reader=sample, publish_timeout=0.01)
        try:
            await bus.publish(ThornEvent("blocked", "test"))
            await asyncio.wait_for(entered.wait(), 1)
            await bus.publish(ThornEvent("queued", "test"))
            await sensor.start()
            await eventually(lambda: sensor.stats["dropped_events"] == 1)
            self.assertEqual(sensor.snapshot, sample())
            self.assertTrue(sensor.stats["fresh"])
        finally:
            await sensor.stop()
            release.set()
            await bus.stop()

    async def test_closed_bus_is_tolerated(self) -> None:
        await self.bus.stop()
        sensor = self.make_sensor(reader=sample)
        await sensor.start()
        await eventually(lambda: sensor.stats["dropped_events"] == 1)
        self.assertEqual(sensor.stats["samples"], 1)

    async def test_configuration_rejects_unsafe_intervals(self) -> None:
        for interval in (0, -1, 0.1, float("inf"), float("nan")):
            with self.subTest(interval=interval), self.assertRaises(ValueError):
                SystemSensor(self.bus, interval=interval)
        for timeout in (0, -1, float("inf"), float("nan")):
            with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                SystemSensor(self.bus, publish_timeout=timeout)


class NativeReaderTests(unittest.TestCase):
    def test_native_calls_produce_only_aggregate_data(self) -> None:
        import psutil

        with (
            patch.object(psutil, "cpu_percent", return_value=12.0) as cpu,
            patch.object(psutil, "cpu_count", return_value=8),
            patch.object(psutil, "virtual_memory", return_value=Mock(total=1000, available=700, percent=30.0)),
            patch.object(psutil, "disk_usage", return_value=Mock(total=2000, free=1000, percent=50.0)),
            patch.object(psutil, "pids", return_value=[1, 2, 3]),
        ):
            snapshot = read_system_snapshot()
        cpu.assert_called_once_with(interval=0.1)
        self.assertEqual(snapshot.cpu_percent, 12.0)
        self.assertEqual(snapshot.process_count, 3)
        self.assertEqual(snapshot.disk_free_bytes, 1000)
