"""Diagnostico por etapas, aviso sin red y buscador local SearXNG, sin trafico real."""

from __future__ import annotations

import json
import ssl
import unittest

import httpcore
import httpx

from core.event_bus import EventBus
from core.network.internet import InternetService
from core.network.network_sensor import NetworkSensor
from tests.test_assistant import MemoryAudit


class FakeBackend:
    def __init__(self, answers: dict[str, object]) -> None:
        self.answers = answers

    async def resolver(self, host: str, port: int) -> list[str]:
        answer = self.answers.get(host, ["93.184.216.34"])
        if isinstance(answer, BaseException):
            raise answer
        return answer


class FakeClient:
    def __init__(self, answers: dict[str, object], failures: dict[str, BaseException] | None = None) -> None:
        self.backend = FakeBackend(answers)
        self.failures = failures or {}
        self.requested: list[str] = []

    async def get(self, url: str, **kwargs: object) -> tuple[bytes, str, str]:
        self.requested.append(url)
        host = httpx.URL(url).host
        if host in self.failures:
            raise self.failures[host]
        return b"<p>ok</p>", "text/html", url

    async def stop(self) -> None:
        pass


class DiagnosisTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.bus = EventBus(); await self.bus.start()
        self.addAsyncCleanup(self.bus.stop)

    def service(self, client: FakeClient, **kwargs: object) -> InternetService:
        service = InternetService(MemoryAudit(), self.bus, enabled=True, client=client,
                                  **{"provider": "wikipedia", **kwargs})
        self.addAsyncCleanup(service.stop)
        return service

    async def test_success_also_checks_the_search_provider(self) -> None:
        client = FakeClient({})
        result = await self.service(client).diagnose(consent=True, identity="a")
        self.assertTrue(result["reachable"])
        self.assertEqual([check["host"] for check in result["checks"]], ["example.com", "es.wikipedia.org"])
        self.assertIsNotNone(result["dns_ms"]); self.assertIsNotNone(result["https_ms"])

    async def test_each_failed_stage_is_named(self) -> None:
        tls = httpcore.ConnectError("handshake")
        tls.__cause__ = ssl.SSLError("CERTIFICATE_VERIFY_FAILED")
        cases = {
            "dns": FakeClient({"example.com": OSError("no dns")}),
            "dns_private": FakeClient({"example.com": ["192.168.0.10"]}),
            "connect": FakeClient({}, {"example.com": httpcore.ConnectError("refused")}),
            "tls": FakeClient({}, {"example.com": tls}),
            "timeout": FakeClient({}, {"example.com": httpcore.ConnectTimeout("slow")}),
        }
        for stage, client in cases.items():
            result = await self.service(client).diagnose(consent=True, identity=stage)
            self.assertFalse(result["reachable"], stage)
            self.assertEqual(result["failed_stage"], stage)
        # Respondio por HTTPS sin una pagina util: la ruta funciona igual.
        answered = await self.service(FakeClient({}, {"example.com": RuntimeError("web_provider_unavailable")})).diagnose(
            consent=True, identity="http")
        self.assertTrue(answered["reachable"]); self.assertEqual(answered["failed_stage"], "http")

    async def test_without_network_fails_fast_and_diagnosis_says_why(self) -> None:
        client = FakeClient({})
        service = self.service(client, offline=lambda: True)
        with self.assertRaisesRegex(RuntimeError, "^web_offline$"):
            await service.search("redes", consent=True, identity="a")
        self.assertEqual(client.requested, [], "no espera a que el DNS falle por tiempo")
        result = await service.diagnose(consent=True, identity="b")
        self.assertEqual(result["failed_stage"], "offline")
        broken_hint = self.service(FakeClient({}), offline=lambda: 1 / 0)
        self.assertFalse(broken_hint.is_offline(), "un sensor roto no bloquea Internet")


class LocalSearchTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.bus = EventBus(); await self.bus.start()
        self.addAsyncCleanup(self.bus.stop)

    def service(self, handler, url: str = "http://127.0.0.1:8888") -> InternetService:
        service = InternetService(MemoryAudit(), self.bus, enabled=True, provider="searxng", client=FakeClient({}),
                                  searxng_url=url, searxng_transport=httpx.MockTransport(handler))
        self.addAsyncCleanup(service.stop)
        return service

    async def test_searxng_results_are_validated_like_any_public_source(self) -> None:
        seen = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, json={"results": [
                {"url": "https://docs.python.org/3/", "title": "Python", "content": "Documentación oficial"},
                {"url": "http://192.168.1.1/admin", "title": "Router", "content": "privado"},
                {"url": "https://example.org/", "title": "", "content": "sin título"}]})

        service = self.service(handler)
        self.assertTrue(service.status["search_available"])
        result = await service.search("python asyncio", consent=True, identity="a")
        self.assertEqual([source["url"] for source in result["sources"]], ["https://docs.python.org/3/"])
        self.assertEqual(result["sources"][0]["excerpt"], "Documentación oficial")
        self.assertEqual(seen[0].url.params["format"], "json")
        self.assertEqual(seen[0].url.host, "127.0.0.1")

    async def test_searxng_errors_explain_what_to_fix(self) -> None:
        disabled = self.service(lambda request: httpx.Response(403, text="Forbidden"))
        with self.assertRaisesRegex(RuntimeError, "^web_local_search_json_disabled$"):
            await disabled.search("redes", consent=True, identity="a")

        def refused(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused", request=request)

        stopped = self.service(refused)
        with self.assertRaisesRegex(RuntimeError, "^web_local_search_unavailable$"):
            await stopped.search("redes", consent=True, identity="b")
        remote = InternetService(MemoryAudit(), self.bus, enabled=True, provider="searxng", client=FakeClient({}),
                                 searxng_url="http://buscador.example:8888")
        self.addAsyncCleanup(remote.stop)
        self.assertEqual(remote.status["state"], "invalid_configuration", "solo loopback")


class OfflineSensorTests(unittest.TestCase):
    def sensor(self, interfaces: list[dict[str, object]], fresh: bool = True) -> NetworkSensor:
        sensor = NetworkSensor(EventBus(), clock=lambda: 10.0)
        sensor.snapshot = {"sampled_at": "", "gateway_ipv4": None, "upload_bps": None, "download_bps": None,
                           "interfaces": interfaces}
        sensor.sample_time = 9.0 if fresh else -100.0
        return sensor

    def test_only_a_fresh_sample_without_usable_addresses_is_offline(self) -> None:
        loopback = {"name": "lo", "up": True, "addresses": ["127.0.0.1", "::1"]}
        apipa = {"name": "Wi-Fi", "up": True, "addresses": ["169.254.10.2", "fe80::1%12"]}
        lan = {"name": "Wi-Fi", "up": True, "addresses": ["192.168.1.20"]}
        ipv6 = {"name": "Ethernet", "up": True, "addresses": ["2001:db8::5"]}
        down = {"name": "Ethernet", "up": False, "addresses": ["192.168.1.30"]}
        self.assertTrue(self.sensor([loopback, apipa, down]).offline())
        self.assertFalse(self.sensor([loopback, lan]).offline())
        self.assertFalse(self.sensor([ipv6]).offline(), "IPv6 sin gateway IPv4 sigue siendo red")
        self.assertFalse(self.sensor([loopback], fresh=False).offline(), "sin muestra vigente no se afirma nada")


if __name__ == "__main__":
    unittest.main()
