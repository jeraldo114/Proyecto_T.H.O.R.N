"""Reglas, cancelacion, acciones cerradas e IA simulada: sin microfono ni GUI."""

from __future__ import annotations

import asyncio
import json
import threading
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import httpx

from core.actions import ActionController
from core.alerts import AlertEngine, AlertRule
from core.audit import AuditStore
from core.event_bus import EventBus
from core.events import EventType, ThornEvent
from core.local_llm import LocalLanguageModel, validate_endpoint
from core.runtime import ThornRuntime


class MemoryAudit:
    """Sustituye MySQL sin activar efectos Windows reales."""

    enabled = True

    def __init__(self) -> None:
        self.rows: list[dict[str, object]] = []
        self.settings: dict[str, object] = {}

    async def append(self, kind: str, status: str, summary: str, details: object = None) -> int:
        self.rows.append({"kind": kind, "status": status, "summary": summary, "details": details})
        return len(self.rows)

    async def finish(self, record_id: int, status: str) -> None:
        self.rows[record_id - 1]["status"] = status

    async def save_setting(self, name: str, value: object) -> None:
        self.settings[name] = value


class AlertTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.bus = EventBus()
        self.audit = MemoryAudit()
        self.now = 0.0
        self.engine = AlertEngine(self.bus, self.audit, clock=lambda: self.now)
        self.serial = 0
        await self.bus.start()

    async def asyncTearDown(self) -> None:
        await self.bus.stop()

    async def sample(self, value: float, *, source: str = "sensor.system", age: float = 0) -> None:
        self.serial += 1
        stamp = datetime.now(timezone.utc) - timedelta(seconds=age) + timedelta(microseconds=self.serial)
        await self.engine.on_sample(ThornEvent(EventType.SYSTEM_SNAPSHOT, source, {
            "sampled_at": stamp.isoformat(), "cpu_percent": value,
            "memory_percent": 30, "disk_percent": 30,
        }))

    async def test_sustained_hysteresis_and_no_spam(self) -> None:
        for now in (0, 4, 8, 12, 16):
            self.now = now
            await self.sample(95)
        self.assertIn("cpu", self.engine.active)
        self.assertEqual(len(self.audit.rows), 1)
        self.now = 18
        await self.sample(88)
        self.assertIn("cpu", self.engine.active)
        await self.sample(79)
        self.assertNotIn("cpu", self.engine.active)
        self.assertEqual([row["status"] for row in self.audit.rows], ["active", "recovered"])

    async def test_short_spike_and_sampling_gap_reset_hold(self) -> None:
        await self.sample(96)
        self.now = 3
        await self.sample(40)
        self.now = 4
        await self.sample(96)
        self.now = 22
        await self.sample(96)
        self.assertFalse(self.engine.active)

    async def test_untrusted_stale_and_invalid_samples_are_ignored(self) -> None:
        await self.sample(99, source="api.local")
        await self.sample(99, age=100)
        await self.sample(float("nan"))
        self.assertFalse(self.engine.active)
        self.assertNotIn("cpu", self.engine._since)

    async def test_invalid_config_does_not_write_or_change(self) -> None:
        original = self.engine.configuration
        rules = self.engine.configuration
        rules["cpu"]["clear_percent"] = 99
        with self.assertRaises(ValueError):
            await self.engine.configure(rules)
        self.assertEqual(self.engine.configuration, original)
        self.assertFalse(self.audit.settings)

    async def test_config_saved_before_applied(self) -> None:
        rules = self.engine.configuration
        rules["cpu"]["threshold_percent"] = 85
        self.audit.save_setting = AsyncMock(side_effect=RuntimeError("db_down"))
        with self.assertRaises(RuntimeError):
            await self.engine.configure(rules)
        self.assertEqual(self.engine.rules["cpu"].threshold_percent, 90)

    def test_boolean_nan_infinity_and_unknown_keys_rejected(self) -> None:
        for bad in (True, float("nan"), float("inf"), "90"):
            with self.assertRaises(ValueError):
                AlertRule.parse({"threshold_percent": bad, "clear_percent": 70,
                                 "hold_seconds": 10, "enabled": True})


class ActionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.audit = MemoryAudit()
        self.executed: list[str] = []
        self.clock = 0.0
        self.actions = ActionController(self.audit, executor=self.executed.append,
                                        supported=True, clock=lambda: self.clock)

    async def test_prepare_has_no_effect_and_confirmation_is_one_use(self) -> None:
        proposal = self.actions.prepare("open_notepad")
        self.assertFalse(self.executed)
        result = await self.actions.confirm(proposal["confirmation_id"])
        self.assertEqual(result["status"], "launched")
        self.assertEqual(self.executed, ["open_notepad"])
        self.assertEqual(self.audit.rows[0]["status"], "launched")
        with self.assertRaises(ValueError):
            await self.actions.confirm(proposal["confirmation_id"])

    async def test_expired_unknown_and_disabled_actions(self) -> None:
        with self.assertRaises(ValueError):
            self.actions.prepare("powershell -Command anything")
        proposal = self.actions.prepare("open_calculator")
        self.clock = 61
        with self.assertRaises(ValueError):
            await self.actions.confirm(proposal["confirmation_id"])
        self.audit.enabled = False
        with self.assertRaises(RuntimeError):
            self.actions.prepare("open_notepad")
        self.assertFalse(self.executed)

    async def test_database_failure_prevents_execution(self) -> None:
        proposal = self.actions.prepare("open_project")
        self.audit.append = AsyncMock(side_effect=RuntimeError("db_down"))
        with self.assertRaises(RuntimeError):
            await self.actions.confirm(proposal["confirmation_id"])
        self.assertFalse(self.executed)

    async def test_executor_failure_is_audited(self) -> None:
        self.actions.executor = lambda _: (_ for _ in ()).throw(OSError("failure"))
        proposal = self.actions.prepare("open_notepad")
        with self.assertRaisesRegex(RuntimeError, "action_failed"):
            await self.actions.confirm(proposal["confirmation_id"])
        self.assertEqual(self.audit.rows[0]["status"], "failed")

    async def test_cancellation_waits_for_native_worker_and_records_outcome(self) -> None:
        started, release = threading.Event(), threading.Event()
        def execute(_: str) -> None:
            started.set()
            release.wait(3)
        self.actions.executor = execute
        proposal = self.actions.prepare("open_notepad")
        task = asyncio.create_task(self.actions.confirm(proposal["confirmation_id"]))
        await asyncio.to_thread(started.wait, 2)
        task.cancel()
        await asyncio.sleep(0)
        self.assertFalse(task.done())
        release.set()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(self.audit.rows[0]["status"], "launched")

    async def test_pending_capacity_and_stop(self) -> None:
        for _ in range(20):
            self.actions.prepare("open_notepad")
        with self.assertRaises(RuntimeError):
            self.actions.prepare("open_notepad")
        self.clock = 62
        self.actions.prepare("open_notepad")
        await self.actions.stop()
        self.assertFalse(self.actions.enabled)
        self.assertFalse(self.actions._pending)

    async def test_standard_user_gate_fails_closed(self) -> None:
        with patch("core.actions.windows_actions_allowed", return_value=False):
            controller = ActionController(self.audit, executor=self.executed.append)
        self.assertFalse(controller.enabled)
        self.assertEqual(controller.catalog(), [])

    async def test_post_launch_audit_failure_reports_uncertainty_and_prevents_replay(self) -> None:
        proposal = self.actions.prepare("open_notepad")
        self.audit.finish = AsyncMock(side_effect=RuntimeError("db_down"))
        with self.assertRaisesRegex(RuntimeError, "action_outcome_unrecorded"):
            await self.actions.confirm(proposal["confirmation_id"])
        self.assertEqual(self.executed, ["open_notepad"])
        with self.assertRaises(ValueError):
            await self.actions.confirm(proposal["confirmation_id"])


class LanguageTests(unittest.IsolatedAsyncioTestCase):
    async def make_model(self, handler: object) -> LocalLanguageModel:
        model = LocalLanguageModel(MemoryAudit(), endpoint="http://127.0.0.1:11434",
                                   model="test:local", transport=httpx.MockTransport(handler))
        self.addAsyncCleanup(model.stop)
        return model

    def test_remote_credentials_paths_and_cloud_models_rejected(self) -> None:
        for url in ("http://example.com:11434", "https://127.0.0.1:11434",
                    "http://localhost:11434", "http://user:pass@127.0.0.1:11434",
                    "http://127.0.0.1:11434/api", "http://127.0.0.1:11434?x=1"):
            with self.assertRaises(ValueError):
                validate_endpoint(url)
        self.assertEqual(validate_endpoint("http://[::1]:11434/"), "http://[::1]:11434")
        with self.assertRaises(ValueError):
            LocalLanguageModel(MemoryAudit(), endpoint="http://127.0.0.1:11434", model="test-cloud")

    async def test_valid_reply_only_suggests_and_does_not_store_prompt(self) -> None:
        requests = []
        def handle(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/tags":
                return httpx.Response(200, json={"models": [{"name": "test:local"}]})
            requests.append(json.loads(request.content))
            return httpx.Response(200, json={"done": True, "message": {"content": json.dumps({
                "reply": "Puedo proponer abrir el Bloc de notas.", "suggested_action": "open_notepad",
            })}})
        model = await self.make_model(handle)
        result = await model.ask("private prompt", context={"telemetry_fresh": False}, query_id="test-query")
        self.assertFalse(result["executed"])
        self.assertEqual(result["suggested_action"], "open_notepad")
        self.assertNotIn("private prompt", json.dumps(model.audit.rows))
        self.assertEqual(requests[0]["stream"], False)
        self.assertIn('"telemetry_fresh": false', requests[0]["messages"][0]["content"])

    async def test_missing_model_and_remote_variant_fail_closed(self) -> None:
        for models in ([], [{"name": "test:local", "remote_host": "cloud.example"}]):
            model = await self.make_model(lambda _: httpx.Response(200, json={"models": models}))
            with self.assertRaisesRegex(RuntimeError, "model_missing"):
                await model.ask("hello", context={}, query_id="test")

    async def test_invalid_json_and_forbidden_action_are_not_executed(self) -> None:
        for content in ("not json", json.dumps({"reply": "x", "suggested_action": "run_shell"}),
                        json.dumps({"reply": "x", "suggested_action": None, "command": "bad"})):
            def handle(request: httpx.Request) -> httpx.Response:
                return httpx.Response(200, json={"models": [{"name": "test:local"}]}) if request.url.path == "/api/tags" else httpx.Response(200, json={"done": True, "message": {"content": content}})
            model = await self.make_model(handle)
            with self.assertRaisesRegex(RuntimeError, "model_query_failed"):
                await model.ask("hello", context={}, query_id="test")

    async def test_engine_down_and_redirect_are_not_followed(self) -> None:
        model = await self.make_model(lambda _: httpx.Response(302, headers={"Location": "https://remote.example"}))
        self.assertEqual((await model.probe())["state"], "engine_unavailable")

    async def test_only_one_query_and_cancellation_closes_busy_state(self) -> None:
        started, release = asyncio.Event(), asyncio.Event()
        async def handle(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/tags":
                return httpx.Response(200, json={"models": [{"name": "test:local"}]})
            started.set()
            await release.wait()
            return httpx.Response(200, json={})
        model = await self.make_model(handle)
        task = asyncio.create_task(model.ask("first", context={}, query_id="test"))
        await started.wait()
        with self.assertRaisesRegex(RuntimeError, "model_busy"):
            await model.ask("second", context={}, query_id="test2")
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertFalse(model.status["busy"])
        self.assertEqual(model.audit.rows[-1]["status"], "cancelled")


class ServiceLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_runtime_without_database_keeps_actions_disabled_and_closes_voice(self) -> None:
        runtime = ThornRuntime(use_database=False, enable_assistant_services=True)
        async with runtime:
            await runtime.bus.join()
            self.assertIsNotNone(runtime.services)
            self.assertFalse(runtime.services.actions.enabled)
            self.assertFalse(runtime.services.context()["telemetry_fresh"])
        self.assertTrue(runtime.services.language.client.is_closed)
        self.assertEqual(runtime.services.voice.status["state"], "closed")

    async def test_disabled_audit_does_not_connect(self) -> None:
        audit = AuditStore(enabled=False)
        with patch("core.audit.db_session", side_effect=AssertionError("no IO")):
            await audit.initialize()
            self.assertIsNone(await audit.append("test", "observed", "test"))
            self.assertEqual(await audit.history(), [])
            self.assertIsNone(await audit.load_setting("alerts"))
            with self.assertRaises(RuntimeError):
                await audit.save_setting("alerts", {})


class ProgressiveReplyTests(unittest.IsolatedAsyncioTestCase):
    async def test_stream_decodes_fragments_and_keeps_action_structured(self) -> None:
        parts = ['{"reply":"Primera frase. ', 'Siguiente \\u00', 'e1."',
                 ',"suggested_action":null}']

        def handle(request):
            if request.url.path == "/api/tags":
                return httpx.Response(200, json={"models": [{"name": "test:local"}]})
            payload = json.loads(request.content)
            self.assertTrue(payload["stream"])
            return httpx.Response(200, content="".join(json.dumps({
                "message": {"content": part}, "done": index == len(parts) - 1,
            }) + "\n" for index, part in enumerate(parts)))

        model = LocalLanguageModel(MemoryAudit(), endpoint="http://127.0.0.1:11434", model="test:local",
                                   transport=httpx.MockTransport(handle))
        self.addAsyncCleanup(model.stop)
        fragments = []
        result = await model.ask("Prueba", context={}, query_id="test", progress=fragments.append)
        self.assertEqual("".join(fragments), result["reply"])
        self.assertEqual(fragments[0], "Primera frase. ")
        self.assertIsNone(result["suggested_action"])
