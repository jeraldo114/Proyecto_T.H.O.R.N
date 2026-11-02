"""Pruebas ASGI completas: lifespan, rutas, autenticacion y limites."""

from __future__ import annotations

import json
import asyncio
import os
import unittest
from unittest.mock import AsyncMock, Mock, patch

# Claves exclusivas de este proceso de pruebas; no se escribe el .env real.
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "thorn_web.settings")
os.environ.setdefault("DJANGO_SECRET_KEY", "test-only-secret-not-for-deployment")
os.environ.setdefault("THORN_API_TOKEN", "test-token")

from asgiref.testing import ApplicationCommunicator
from django.core.asgi import get_asgi_application
from django.test import override_settings
from django.urls import get_resolver

from core.events import EventType, ThornEvent
from core.runtime import RuntimeState, ThornRuntime
from core.system_sensor import SystemSensor, SystemSnapshot
from thorn_web.lifespan import ASGIMessage, ASGIScope, ThornASGI

DJANGO_APP = get_asgi_application()
get_resolver().url_patterns


class WebTests(unittest.IsolatedAsyncioTestCase):
    async def test_hud_index_is_local_and_does_not_embed_token(self) -> None:
        with patch("api.hud._read_asset", return_value=(b"<html>T.H.O.R.N</html>", "text/html")):
            status, body, headers = await self.request("/")
        self.assertEqual(status, 200)
        self.assertNotIn(b"test-token", body)
        self.assertIn(b"script-src 'self'", headers[b"content-security-policy"])
        self.assertEqual(headers[b"x-frame-options"], b"DENY")

    async def test_unknown_asset_fails_closed(self) -> None:
        with patch("api.hud._read_asset", side_effect=FileNotFoundError):
            status, body, _ = await self.request("/hud/assets/private.env")
        self.assertEqual(status, 404)
        self.assertEqual(json.loads(body)["error"], "hud_asset_unavailable")

    async def test_activity_requires_token_and_excludes_event_payload(self) -> None:
        status, _, _ = await self.request("/api/v1/activity/")
        self.assertEqual(status, 401)
        await self.runtime.bus.publish(ThornEvent(
            EventType.COMMAND_RECEIVED, "test", {"private": "never-export-this"},
        ))
        await self.runtime.bus.join()
        status, body, _ = await self.request("/api/v1/activity/", token="test-token")
        data = json.loads(body)
        self.assertEqual(status, 200)
        self.assertFalse(data["durable"])
        self.assertEqual(data["capacity"], 120)
        self.assertTrue(any(item["event_type"] == "command.received" for item in data["events"]))
        self.assertNotIn(b"never-export-this", body)

    async def asyncSetUp(self) -> None:
        overrides = override_settings(THORN_API_TOKEN="test-token", THORN_USE_DATABASE=False)
        overrides.enable()
        self.addCleanup(overrides.disable)
        self.runtime = ThornRuntime(use_database=False)
        self.app = ThornASGI(DJANGO_APP, lambda: self.runtime)
        self.lifespan = ApplicationCommunicator(self.app, {"type": "lifespan"})
        await self.lifespan.send_input({"type": "lifespan.startup"})
        self.assertEqual(
            await self.lifespan.receive_output(), {"type": "lifespan.startup.complete"},
        )

    async def asyncTearDown(self) -> None:
        await self.lifespan.send_input({"type": "lifespan.shutdown"})
        self.assertEqual(
            await self.lifespan.receive_output(), {"type": "lifespan.shutdown.complete"},
        )
        await self.lifespan.wait()
        self.assertEqual(self.runtime.state, RuntimeState.STOPPED)
        self.assertEqual(self.runtime.bus.stats.workers, 0)

    async def request(
        self, path: str, *, method: str = "GET", body: bytes = b"",
        token: str | None = None, content_type: str = "application/json",
        host: str = "127.0.0.1", chunks: list[bytes] | None = None,
        query: str = "",
        extra_headers: list[tuple[bytes, bytes]] | None = None,
        app: ThornASGI | None = None,
    ) -> tuple[int, bytes, dict[bytes, bytes]]:
        """Envia mensajes ASGI reales sin abrir sockets ni conectar a MySQL."""
        headers = [(b"host", host.encode()), (b"content-type", content_type.encode())]
        if token is not None:
            headers.append((b"authorization", f"Bearer {token}".encode()))
        headers.extend(extra_headers or [])
        scope: ASGIScope = {
            "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
            "method": method, "path": path, "raw_path": path.encode(),
            "query_string": query.encode(), "scheme": "http", "headers": headers,
            "server": ("127.0.0.1", 8000), "client": ("127.0.0.1", 50000),
        }
        communicator = ApplicationCommunicator(app or self.app, scope)
        try:
            bodies = chunks if chunks is not None else [body]
            for index, chunk in enumerate(bodies):
                await communicator.send_input({
                    "type": "http.request", "body": chunk,
                    "more_body": index < len(bodies) - 1,
                })
            start = await communicator.receive_output()
            self.assertEqual(start["type"], "http.response.start")
            response = b""
            while True:
                message = await communicator.receive_output()
                response += message.get("body", b"")
                if not message.get("more_body", False):
                    break
            await communicator.wait()
            return start["status"], response, {
                name.lower(): value for name, value in start["headers"]
            }
        finally:
            communicator.stop()

    async def test_health_and_status_share_runtime(self) -> None:
        status, body, headers = await self.request("/api/v1/health/")
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)["ready"])
        self.assertEqual(headers[b"cache-control"], b"no-store")
        status, body, _ = await self.request("/api/v1/status/")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertEqual(payload["runtime"]["state"], "running")
        self.assertFalse(payload["runtime"]["database_enabled"])
        self.assertGreaterEqual(payload["events"]["published"], 1)

    async def test_authenticated_event_reaches_subscriber(self) -> None:
        seen: list[ThornEvent] = []

        async def handler(event: ThornEvent) -> None:
            seen.append(event)

        self.runtime.bus.subscribe(EventType.COMMAND_RECEIVED, handler)
        status, body, _ = await self.request(
            "/api/v1/events/", method="POST", token="test-token",
            body=json.dumps({
                "event_type": "command.received", "payload": {"command": "status"},
                "correlation_id": "operation-1",
            }).encode(),
        )
        self.assertEqual(status, 202)
        await self.runtime.bus.join()
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0].event_id, json.loads(body)["event_id"])
        self.assertEqual(seen[0].correlation_id, "operation-1")
        self.assertEqual(seen[0].source, "api.local")

    async def test_missing_or_wrong_token_is_rejected(self) -> None:
        for token in (None, "wrong-token"):
            with self.subTest(token=token):
                status, body, headers = await self.request(
                    "/api/v1/events/", method="POST", token=token, body=b"{}",
                )
                self.assertEqual(status, 401)
                self.assertEqual(json.loads(body)["error"], "unauthorized")
                self.assertEqual(headers[b"www-authenticate"], b"Bearer")

    async def test_validation_rejects_bad_json_and_reserved_events(self) -> None:
        cases = [
            b"not-json", b"[]",
            b'{"event_type":"runtime.stopping"}',
            b'{"event_type":"command.received","payload":[]}',
            b'{"event_type":"command.received","correlation_id":4}',
            b'{"event_type":"command.received","correlation_id":""}',
            b'{"event_type":["command.received"]}',
        ]
        for body in cases:
            with self.subTest(body=body):
                status, _, _ = await self.request(
                    "/api/v1/events/", method="POST", token="test-token", body=body,
                )
                self.assertEqual(status, 400)

    async def test_wrong_content_type_is_rejected(self) -> None:
        status, _, _ = await self.request(
            "/api/v1/events/", method="POST", token="test-token",
            content_type="text/plain", body=b"{}",
        )
        self.assertEqual(status, 415)

    async def test_invalid_host_and_method_are_rejected(self) -> None:
        status, _, _ = await self.request("/api/v1/health/", host="untrusted.example")
        self.assertEqual(status, 400)
        status, _, headers = await self.request("/api/v1/events/")
        self.assertEqual(status, 405)
        self.assertEqual(headers[b"allow"], b"POST")

    async def test_oversized_chunked_body_is_rejected(self) -> None:
        status, body, _ = await self.request(
            "/api/v1/events/", method="POST", token="test-token",
            chunks=[b"x" * 8192, b"y" * 8193],
        )
        self.assertEqual(status, 413)
        self.assertEqual(json.loads(body)["error"], "request_too_large")

    async def test_unstarted_runtime_returns_503(self) -> None:
        app = ThornASGI(DJANGO_APP, lambda: ThornRuntime(use_database=False))
        status, body, _ = await self.request("/api/v1/health/", app=app)
        self.assertEqual(status, 503)
        self.assertFalse(json.loads(body)["ready"])

    async def test_closed_bus_rejects_publication(self) -> None:
        await self.runtime.bus.stop()
        status, body, _ = await self.request(
            "/api/v1/events/", method="POST", token="test-token",
            body=b'{"event_type":"system.alert"}',
        )
        self.assertEqual(status, 503)
        self.assertEqual(json.loads(body)["error"], "bus_unavailable")

    async def test_system_requires_authentication_even_without_sensor(self) -> None:
        for token in (None, "wrong-token"):
            status, _, headers = await self.request("/api/v1/system/", token=token)
            self.assertEqual(status, 401)
            self.assertEqual(headers[b"www-authenticate"], b"Bearer")
        status, body, _ = await self.request("/api/v1/system/", token="test-token")
        self.assertEqual(status, 503)
        self.assertEqual(json.loads(body)["error"], "sensor_disabled")

    async def test_system_uses_cache_without_reading_per_request(self) -> None:
        sample = SystemSnapshot("2026-01-01T00:00:00+00:00", 10.0, 8, 1000, 800, 20.0, 2000, 1000, 50.0, 42)
        reader = Mock(return_value=sample)
        sensor = SystemSensor(self.runtime.bus, interval=60, reader=reader)
        self.runtime.system_sensor = sensor
        status, body, _ = await self.request("/api/v1/system/", token="test-token")
        self.assertEqual(status, 503)
        self.assertIsNone(json.loads(body)["snapshot"])
        await sensor.start()
        async with asyncio.timeout(2):
            while sensor.snapshot is None:
                await asyncio.sleep(0.005)
        for _ in range(2):
            status, body, headers = await self.request("/api/v1/system/", token="test-token")
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body)["snapshot"]["process_count"], 42)
            self.assertEqual(headers[b"cache-control"], b"no-store")
        reader.assert_called_once()
        await sensor.stop()
        status, body, _ = await self.request("/api/v1/system/", token="test-token")
        self.assertEqual(status, 503)
        self.assertFalse(json.loads(body)["sensor"]["fresh"])

    async def test_snapshot_event_cannot_be_forged_from_api(self) -> None:
        status, _, _ = await self.request(
            "/api/v1/events/", method="POST", token="test-token",
            body=b'{"event_type":"system.snapshot"}',
        )
        self.assertEqual(status, 400)


class LifespanTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_start_reports_failure_and_cleans_resources(self) -> None:
        runtime = Mock(start=AsyncMock(side_effect=ConnectionError("expected")), stop=AsyncMock())
        app = ThornASGI(AsyncMock(), lambda: runtime)
        communicator = ApplicationCommunicator(app, {"type": "lifespan"})
        with self.assertLogs("thorn_web.lifespan", level="ERROR"):
            await communicator.send_input({"type": "lifespan.startup"})
            message = await communicator.receive_output()
            self.assertEqual(message["type"], "lifespan.startup.failed")
            await communicator.wait()
        runtime.stop.assert_awaited_once()

    async def test_failed_shutdown_is_reported_once(self) -> None:
        runtime = Mock(start=AsyncMock(), stop=AsyncMock(side_effect=ConnectionError("expected")))
        app = ThornASGI(AsyncMock(), lambda: runtime)
        communicator = ApplicationCommunicator(app, {"type": "lifespan"})
        await communicator.send_input({"type": "lifespan.startup"})
        await communicator.receive_output()
        with self.assertLogs("thorn_web.lifespan", level="ERROR"):
            await communicator.send_input({"type": "lifespan.shutdown"})
            message: ASGIMessage = await communicator.receive_output()
            self.assertEqual(message["type"], "lifespan.shutdown.failed")
            await communicator.wait()
        runtime.stop.assert_awaited_once()
