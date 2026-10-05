"""Pruebas de entrega, saturacion y cancelacion sin servicios externos."""

from __future__ import annotations

import asyncio
import unittest

from core.event_bus import EventBus, EventBusClosedError
from core.events import EventPriority, ThornEvent


class EventBusTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.bus = EventBus(worker_count=1, handler_timeout=0.1)
        await self.bus.start()

    async def asyncTearDown(self) -> None:
        await self.bus.stop(timeout=0.1)

    async def test_priority_and_fifo_for_equal_priority(self) -> None:
        seen: list[str] = []

        async def handler(event: ThornEvent) -> None:
            seen.append(str(event.payload["value"]))

        self.bus.subscribe("test", handler)
        for value, priority in (
            ("low", EventPriority.LOW), ("normal-1", EventPriority.NORMAL),
            ("critical", EventPriority.CRITICAL), ("normal-2", EventPriority.NORMAL),
        ):
            await self.bus.publish(ThornEvent(
                "test", "test", {"value": value}, priority=priority,
            ))
        await asyncio.wait_for(self.bus.join(), 1)
        self.assertEqual(seen, ["critical", "normal-1", "normal-2", "low"])

    async def test_failure_does_not_skip_other_subscribers(self) -> None:
        seen: list[str] = []

        async def broken(event: ThornEvent) -> None:
            raise ValueError("expected")

        async def healthy(event: ThornEvent) -> None:
            seen.append(event.event_id)

        self.bus.subscribe("test", broken)
        self.bus.subscribe("test", healthy)
        self.bus.subscribe("test", healthy)
        event = ThornEvent("test", "test")
        with self.assertLogs("core.event_bus", level="ERROR"):
            await self.bus.publish(event)
            await asyncio.wait_for(self.bus.join(), 1)
        self.assertEqual(seen, [event.event_id])
        self.assertEqual(self.bus.stats.handler_errors, 1)

    async def test_timeout_keeps_worker_alive(self) -> None:
        seen: list[str] = []

        async def stalled(event: ThornEvent) -> None:
            await asyncio.Event().wait()

        async def healthy(event: ThornEvent) -> None:
            seen.append(event.event_id)

        self.bus.subscribe("test", stalled)
        self.bus.subscribe("test", healthy)
        with self.assertLogs("core.event_bus", level="ERROR"):
            await self.bus.publish(ThornEvent("test", "test"))
            await asyncio.wait_for(self.bus.join(), 1)
        self.assertEqual(len(seen), 1)
        self.assertEqual(self.bus.stats.workers, 1)

    async def test_handler_cancelled_error_does_not_kill_worker(self) -> None:
        seen: list[str] = []

        async def cancelled(event: ThornEvent) -> None:
            raise asyncio.CancelledError

        async def healthy(event: ThornEvent) -> None:
            seen.append(event.event_id)

        self.bus.subscribe("test", cancelled)
        self.bus.subscribe("test", healthy)
        with self.assertLogs("core.event_bus", level="WARNING"):
            await self.bus.publish(ThornEvent("test", "test"))
            await asyncio.wait_for(self.bus.join(), 1)
        self.assertEqual(len(seen), 1)
        self.assertEqual(self.bus.stats.handler_errors, 1)

    async def test_unsubscribe_and_reject_sync_handler(self) -> None:
        async def handler(event: ThornEvent) -> None:
            return

        self.bus.subscribe("test", handler)
        self.assertTrue(self.bus.unsubscribe("test", handler))
        self.assertFalse(self.bus.unsubscribe("test", handler))
        with self.assertRaises(TypeError):
            self.bus.subscribe("test", lambda event: None)  # type: ignore[arg-type]

    async def test_close_wakes_blocked_producer_and_cancels_handler(self) -> None:
        bus = EventBus(max_queue_size=1, worker_count=1, handler_timeout=10)
        entered = asyncio.Event()
        cancelled = asyncio.Event()

        async def stalled(event: ThornEvent) -> None:
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        bus.subscribe("test", stalled)
        await bus.start()
        producer: asyncio.Task[None] | None = None
        try:
            await bus.publish(ThornEvent("test", "first"))
            await asyncio.wait_for(entered.wait(), 1)
            await bus.publish(ThornEvent("test", "second"))
            producer = asyncio.create_task(bus.publish(ThornEvent("test", "third")))
            await asyncio.sleep(0)
            self.assertFalse(producer.done())
            with self.assertLogs("core.event_bus", level="WARNING"):
                await asyncio.wait_for(bus.stop(timeout=0.01), 1)
            with self.assertRaises(EventBusClosedError):
                await producer
            self.assertTrue(cancelled.is_set())
            self.assertEqual(bus.stats.discarded, 2)
            self.assertEqual(bus.stats.queued, 0)
            self.assertEqual(bus.stats.workers, 0)
            await asyncio.wait_for(bus.join(), 1)
        finally:
            if producer is not None:
                producer.cancel()
                await asyncio.gather(producer, return_exceptions=True)
            await bus.stop(timeout=0.1)

    async def test_handler_cannot_deadlock_full_queue(self) -> None:
        bus = EventBus(max_queue_size=1, worker_count=1)
        entered = asyncio.Event()
        resume = asyncio.Event()
        rejected = asyncio.Event()

        async def handler(event: ThornEvent) -> None:
            entered.set()
            await resume.wait()
            with self.assertRaises(asyncio.QueueFull):
                await bus.publish(ThornEvent("child", "handler"))
            rejected.set()

        bus.subscribe("parent", handler)
        await bus.start()
        try:
            await bus.publish(ThornEvent("parent", "test"))
            await asyncio.wait_for(entered.wait(), 1)
            await bus.publish(ThornEvent("queued", "test"))
            resume.set()
            await asyncio.wait_for(bus.join(), 1)
            self.assertTrue(rejected.is_set())
        finally:
            await bus.stop(timeout=0.1)

    async def test_handler_cannot_join_or_stop_itself(self) -> None:
        checked = asyncio.Event()

        async def handler(event: ThornEvent) -> None:
            with self.assertRaises(RuntimeError):
                await self.bus.join()
            with self.assertRaises(RuntimeError):
                await self.bus.stop()
            checked.set()

        self.bus.subscribe("test", handler)
        await self.bus.publish(ThornEvent("test", "test"))
        await asyncio.wait_for(self.bus.join(), 1)
        self.assertTrue(checked.is_set())

    async def test_reject_publication_after_stop(self) -> None:
        await self.bus.stop()
        with self.assertRaises(EventBusClosedError):
            await self.bus.publish(ThornEvent("test", "test"))

    async def test_payload_is_copied(self) -> None:
        payload = {"value": 1}
        event = ThornEvent("test", "test", payload)
        payload["value"] = 2
        self.assertEqual(event.payload["value"], 1)
        with self.assertRaises(TypeError):
            event.payload["value"] = 3  # type: ignore[index]
