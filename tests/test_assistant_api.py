"""Contratos HTTP privados del asistente, con ejecutor y modelo controlados."""

from __future__ import annotations

import json
import unittest

import httpx
from asgiref.testing import ApplicationCommunicator
from django.test import override_settings

from tests import test_web as web
from tests.test_assistant import MemoryAudit
from core.actions import ActionController
from core.local_llm import LocalLanguageModel
from core.elevenlabs_voice import ElevenLabsVoice, VoiceConfig
from core.runtime import ThornRuntime
from core.speech_input import LocalSpeechInput, MAX_PCM_BYTES, TRANSCRIBE_PATH
from thorn_web.lifespan import ThornASGI


class AssistantAPITests(unittest.IsolatedAsyncioTestCase):
    request = web.WebTests.request

    async def asyncSetUp(self) -> None:
        self.override = override_settings(THORN_API_TOKEN="test-token", THORN_USE_DATABASE=False)
        self.override.enable()
        self.addCleanup(self.override.disable)
        self.runtime = ThornRuntime(use_database=False, enable_assistant_services=True)
        self.app = ThornASGI(web.DJANGO_APP, lambda: self.runtime)
        self.lifespan = ApplicationCommunicator(self.app, {"type": "lifespan"})
        await self.lifespan.send_input({"type": "lifespan.startup"})
        self.assertEqual(await self.lifespan.receive_output(), {"type": "lifespan.startup.complete"})
        self.services = self.runtime.services
        # La suite HTTP no depende de que un proceso Ollama este instalado/activo.
        from unittest.mock import AsyncMock
        self.services.language.probe = AsyncMock(return_value=self.services.language.status)
        self.executed: list[str] = []
        self.audit = MemoryAudit()
        self.services.actions = ActionController(self.audit, supported=True, executor=self.executed.append)

    async def asyncTearDown(self) -> None:
        await self.lifespan.send_input({"type": "lifespan.shutdown"})
        self.assertEqual(await self.lifespan.receive_output(), {"type": "lifespan.shutdown.complete"})
        await self.lifespan.wait()

    async def post(self, path: str, data: object) -> tuple[int, dict[str, object]]:
        status, raw, _ = await self.request("/api/v1/" + path + "/", method="POST",
                                           body=json.dumps(data).encode(), token="test-token")
        return status, json.loads(raw)

    async def test_private_chat_delivers_delta_then_final_result(self) -> None:
        async def remote(request):
            if request.url.path == "/api/tags":
                return httpx.Response(200, json={"models": [{"name": "test:local"}]})
            event = {"message": {"content": '{"reply":"Respuesta de prueba.","suggested_action":null}'}, "done": True}
            return httpx.Response(200, content=json.dumps(event) + "\n")
        await self.services.language.stop()
        self.services.language = LocalLanguageModel(self.audit, endpoint="http://127.0.0.1:11434",
            model="test:local", transport=httpx.MockTransport(remote))
        status, raw, headers = await self.request("/api/v1/chat/", method="POST",
            body=b'{"prompt":"Que sabes de electronica?"}', token="test-token",
            extra_headers=[(b"accept", b"application/x-ndjson")])
        self.assertEqual(status, 200)
        self.assertEqual(headers[b"content-type"], b"application/x-ndjson")
        delta, final = [json.loads(line) for line in raw.splitlines()]
        self.assertEqual(delta["delta"], final["result"]["reply"])
        self.assertIsNone(final["result"]["suggested_action"])

    async def test_all_routes_require_authentication(self) -> None:
        for route in ("capabilities", "alerts", "history", "memory", "network"):
            status, _, _ = await self.request(f"/api/v1/{route}/")
            self.assertEqual(status, 401)
        for route in ("alerts/configure", "actions/prepare", "actions/confirm", "chat", "memory/save", "memory/delete", "voice/speech", "microphone/transcribe", "web/search", "web/read", "network/diagnose"):
            status, _, _ = await self.request(f"/api/v1/{route}/", method="POST", body=b"{}")
            self.assertEqual(status, 401)
        self.assertFalse(self.executed)

    async def test_conversation_archive_scopes_and_explicit_forgetting(self) -> None:
        from unittest.mock import AsyncMock
        memory = self.services.conversations
        memory.page = AsyncMock(return_value={"records": [], "next_before": None, "durable": True})
        memory.clear = AsyncMock(return_value=2)
        memory.recording_paused = AsyncMock(return_value=False)
        chat_only = self.runtime.sessions.issue(frozenset({'assistant:chat'}))['access_token']
        for route in ('conversation/history', 'conversation/forget', 'conversation/modify'):
            status, _, _ = await self.request('/api/v1/' + route + '/', method='POST', token=chat_only, body=b'{}')
            self.assertEqual(status, 403)
        memory.page.assert_not_awaited(); memory.clear.assert_not_awaited()
        status, value = await self.post('conversation/history', {'query': 'mi proyecto'})
        self.assertEqual(status, 200)
        memory.page.assert_awaited_once_with(before=None, limit=20, query='mi proyecto')
        state = self.services.operator.store.state('authority')
        self.services.operator.store.remember('authority', state, 'dato privado', 'respuesta')
        status, value = await self.post('conversation/forget', {'confirmation': 'BORRAR MEMORIA'})
        self.assertEqual(status, 200); self.assertEqual(value['deleted'], 2)
        self.assertEqual(self.services.operator.store.snapshot('authority')['context_messages'], 0)

    async def test_reconnected_operator_recalls_archive_but_chat_only_session_cannot(self) -> None:
        from unittest.mock import AsyncMock, Mock
        outgoing, archive = [], []
        async def remote(request):
            if request.url.path == '/api/tags':
                return httpx.Response(200, json={'models': [{'name': 'test:local'}]})
            outgoing.append(json.loads(request.content))
            return httpx.Response(200, json={'done': True, 'message': {'content': json.dumps({
                'reply': 'El proyecto se llama Espino Azul.', 'suggested_action': None})}})
        async def append(prompt, reply, **kwargs):
            archive.append({'date': '2026-11-02', 'user': prompt, 'assistant': reply}); return True
        memory = Mock(available=True, errors=0, epoch=AsyncMock(return_value=0),
                      recall=AsyncMock(side_effect=lambda prompt: (0, list(archive))),
                      append=AsyncMock(side_effect=append))
        self.services.conversations = memory
        await self.services.language.stop()
        self.services.language = LocalLanguageModel(self.audit, endpoint='http://127.0.0.1:11434',
            model='test:local', transport=httpx.MockTransport(remote))
        first = self.runtime.sessions.issue()['access_token']
        status, _, _ = await self.request('/api/v1/chat/', method='POST', token=first,
            body=json.dumps({'prompt': 'Mi proyecto se llama Espino Azul.'}).encode())
        self.assertEqual(status, 200)
        self.runtime.sessions.revoke(first)
        second = self.runtime.sessions.issue()['access_token']
        status, _, _ = await self.request('/api/v1/chat/', method='POST', token=second,
            body=json.dumps({'prompt': '¿Cómo se llama mi proyecto?'}).encode())
        self.assertEqual(status, 200)
        self.assertIn('Espino Azul', outgoing[-1]['messages'][0]['content'])
        restricted = self.runtime.sessions.issue(frozenset({'assistant:chat'}))['access_token']
        status, _, _ = await self.request('/api/v1/chat/', method='POST', token=restricted,
            body=b'{"prompt":"hola"}')
        self.assertEqual(status, 200)
        self.assertNotIn('Espino Azul', outgoing[-1]['messages'][0]['content'])
        self.assertEqual(memory.append.await_count, 2)

    async def test_session_exchange_rotation_scopes_and_revocation(self) -> None:
        status, _, _ = await self.request('/api/v1/auth/session/', method='POST', body=b'{}')
        self.assertEqual(status, 401)
        status, issued = await self.post('auth/session', {})
        self.assertEqual(status, 200)
        token = issued['access_token']
        status, _, _ = await self.request('/api/v1/system/', token=token)
        self.assertEqual(status, 503)  # autenticado; este runtime de prueba no habilita el sensor
        status, raw, headers = await self.request('/api/v1/auth/renew/', method='POST', body=b'{}', token=token)
        self.assertEqual(status, 200); self.assertEqual(headers[b'cache-control'], b'no-store')
        rotated = json.loads(raw)['access_token']; self.assertNotEqual(rotated, token)
        status, _, _ = await self.request('/api/v1/auth/renew/', method='POST', body=b'{}', token=token)
        self.assertEqual(status, 401)
        status, _, _ = await self.request('/api/v1/auth/revoke/', method='POST', body=b'{}', token=rotated)
        self.assertEqual(status, 200)
        status, _, _ = await self.request('/api/v1/activity/', token=rotated)
        self.assertEqual(status, 401)
        restricted = self.runtime.sessions.issue(frozenset({'telemetry:read'}))['access_token']
        status, _, _ = await self.request(TRANSCRIBE_PATH, method='POST', token=restricted,
            content_type='application/octet-stream', body=b'\0' * 6400,
            extra_headers=[(b'x-thorn-microphone-consent', b'local')])
        self.assertEqual(status, 403)
        status, _, _ = await self.request('/api/v1/chat/', method='POST', token=restricted, body=b'{"prompt":"hello"}')
        self.assertEqual(status, 403)

    async def test_local_voice_uses_wav_without_cloud_authorization(self) -> None:
        from core.local_voice import LocalVoice
        from tests.test_local_autonomy import wav_fixture
        await self.services.voice.stop()
        self.services.voice = LocalVoice(self.audit, enabled=True, loader=lambda path: object(),
            synthesizer=lambda model, text: wav_fixture())
        await self.services.voice.start()
        issued = self.runtime.sessions.issue()['access_token']
        status, raw, headers = await self.request('/api/v1/voice/speech/', method='POST', token=issued,
            body=b'{"text":"texto privado","cloud_consent":false}')
        self.assertEqual(status, 200); self.assertEqual(headers[b'content-type'], b'audio/wav')
        self.assertEqual(headers[b'x-thorn-voice-provider'], b'piper'); self.assertTrue(raw.startswith(b'RIFF'))
        self.assertNotIn('texto privado', json.dumps(self.audit.rows))

    async def test_web_consent_is_required_and_normal_chat_stays_local(self) -> None:
        from core.internet import InternetService
        from tests.test_network import FakeWebClient
        client = FakeWebClient({})
        await self.services.internet.stop()
        self.services.internet = InternetService(self.audit, self.runtime.bus, enabled=True,
                                                 provider="wikipedia", client=client)
        for route, data in (("web/search", {"query": "prueba", "internet_consent": False}),
                            ("web/read", {"url": "https://example.com", "internet_consent": False}),
                            ("network/diagnose", {"internet_consent": False})):
            status, result = await self.post(route, data)
            self.assertEqual(status, 400); self.assertEqual(result["error"], "web_internet_consent_required")
        self.assertFalse(client.calls)
        status, _, _ = await self.request("/api/v1/network/", token="test-token")
        self.assertEqual(status, 200); self.assertFalse(client.calls)
        self.assertFalse(self.executed)

    async def test_web_sources_return_without_model_and_without_private_context_or_actions(self) -> None:
        from unittest.mock import AsyncMock
        from core.internet import InternetService
        from tests.test_network import FakeWebClient
        client = FakeWebClient({"query": {"pages": [{"title": "Fuente", "index": 1,
            "fullurl": "https://es.wikipedia.org/wiki/Fuente", "extract": "Informacion real de la fuente."}]}})
        await self.services.internet.stop()
        self.services.language.probe = AsyncMock(return_value={"available": False})
        self.services.internet = InternetService(self.audit, self.runtime.bus, enabled=True,
                                                 provider="wikipedia", client=client)
        status, result = await self.post("web/search", {"query": "consulta", "internet_consent": True})
        self.assertEqual(status, 200); self.assertEqual(result["summary_method"], "extractive")
        self.assertIsNone(result["suggested_action"]); self.assertFalse(result["executed"])
        self.assertEqual(result["source"], "internet"); self.assertEqual(len(result["sources"]), 1)
        self.assertFalse(self.executed)
        self.assertNotIn("consulta", json.dumps(self.audit.rows))
        self.assertNotIn("user_notes_untrusted", json.dumps(result))

    async def test_private_urls_and_unexpected_fields_are_rejected(self) -> None:
        status, result = await self.post("web/read", {"url": "https://127.0.0.1:8004", "internet_consent": True})
        self.assertEqual(status, 400); self.assertEqual(result["error"], "web_public_https_required")
        status, result = await self.post("network/diagnose", {"target": "192.168.1.1", "internet_consent": True})
        self.assertEqual(status, 400); self.assertEqual(result["error"], "invalid_fields")

    async def test_conversation_preference_is_private_and_separate_from_cloud_consent(self) -> None:
        self.services.conversation_auto_start = True
        status, raw, _ = await self.request("/api/v1/capabilities/", token="test-token")
        self.assertEqual(status, 200)
        data = json.loads(raw)
        self.assertEqual(data["conversation"], {"auto_start": True, "vad": "silero_local", "max_seconds": 15})
        self.assertFalse(data["microphone"]["cloud"])
        self.assertNotIn("cloud_consent", data["conversation"])

    async def test_local_pcm_is_authenticated_across_chunks_without_widening_json_limits(self) -> None:
        await self.services.microphone.stop()
        engine = LocalSpeechInput(self.audit, enabled=True, loader=lambda path: object(),
                                  decoder=lambda model, pcm: "sistema")
        self.services.microphone = engine
        await engine.start()
        status, raw, headers = await self.request(TRANSCRIBE_PATH, method="POST", token="test-token",
            content_type="application/octet-stream", chunks=[b"\0" * 20000, b"\0" * 20000],
            extra_headers=[(b"x-thorn-microphone-consent", b"local")])
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(raw)["text"], "sistema")
        self.assertFalse(json.loads(raw)["cloud"])
        self.assertEqual(headers[b"cache-control"], b"no-store")
        self.assertEqual([row["status"] for row in self.audit.rows], ["requested", "transcribed"])
        self.assertFalse(self.executed, "El dictado no ejecuta consultas ni acciones.")
        status, _, _ = await self.request("/api/v1/chat/", method="POST", token="test-token", body=b"x" * 20000)
        self.assertEqual(status, 413)

    async def test_microphone_requires_consent_and_pcm_format(self) -> None:
        status, raw, _ = await self.request(TRANSCRIBE_PATH, method="POST", token="test-token",
                                            content_type="application/octet-stream", body=b"\0" * 6400)
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(raw)["error"], "microphone_local_consent_required")
        status, raw, _ = await self.request(TRANSCRIBE_PATH, method="POST", token="test-token", body=b"{}",
            extra_headers=[(b"x-thorn-microphone-consent", b"local")])
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(raw)["error"], "microphone_pcm_required")

    async def test_audio_unauthenticated_and_oversized_requests_fail_before_decode(self) -> None:
        status, _, _ = await self.request(TRANSCRIBE_PATH, method="POST", body=b"x" * 40000)
        self.assertEqual(status, 401)
        status, _, _ = await self.request(TRANSCRIBE_PATH, method="POST", token="test-token",
            content_type="application/octet-stream", chunks=[b"x" * 240000, b"x" * 240002],
            extra_headers=[(b"x-thorn-microphone-consent", b"local")])
        self.assertEqual(status, 413)

    async def test_prepare_confirm_and_replay_contract(self) -> None:
        status, prepared = await self.post("actions/prepare", {"action_id": "open_notepad"})
        self.assertEqual(status, 202)
        self.assertFalse(self.executed)
        status, result = await self.post("actions/confirm", {"confirmation_id": prepared["confirmation_id"]})
        self.assertEqual(status, 200)
        self.assertEqual(result["status"], "launched")
        status, _ = await self.post("actions/confirm", {"confirmation_id": prepared["confirmation_id"]})
        self.assertEqual(status, 400)
        self.assertEqual(self.executed, ["open_notepad"])

    async def test_free_commands_paths_and_extra_keys_are_rejected(self) -> None:
        for payload in ({"action_id": "cmd.exe"}, {"action_id": "open_notepad", "path": "C:/bad.exe"},
                        {"action_id": []}, {"command": "del *"}):
            status, _ = await self.post("actions/prepare", payload)
            self.assertEqual(status, 400)
        self.assertFalse(self.executed)

    async def test_alert_rules_require_database_and_validate_before_write(self) -> None:
        status, rules, _ = await self.request("/api/v1/alerts/", token="test-token")
        self.assertEqual(status, 200)
        data = json.loads(rules)
        status, _ = await self.post("alerts/configure", {"rules": data["rules"]})
        self.assertEqual(status, 503)
        data["rules"]["cpu"]["enabled"] = "true"
        status, _ = await self.post("alerts/configure", {"rules": data["rules"]})
        self.assertEqual(status, 400)

    async def test_history_disabled_is_not_presented_as_durable(self) -> None:
        status, raw, _ = await self.request("/api/v1/history/", token="test-token")
        self.assertEqual(status, 503)
        self.assertEqual(json.loads(raw)["error"], "database_required")

    async def test_model_reply_cannot_bypass_action_confirmation(self) -> None:
        await self.services.language.stop()
        def handle(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/tags":
                return httpx.Response(200, json={"models": [{"name": "test:local"}]})
            return httpx.Response(200, json={"done": True, "message": {"content": json.dumps({
                "reply": "Propuesta de apertura", "suggested_action": "open_notepad",
            })}})
        self.services.language = LocalLanguageModel(self.audit, endpoint="http://127.0.0.1:11434",
                                                    model="test:local", transport=httpx.MockTransport(handle))
        status, result = await self.post("chat", {"prompt": "abre notas"})
        self.assertEqual(status, 200)
        self.assertFalse(result["executed"])
        self.assertFalse(self.executed)

    async def test_invalid_chat_payloads_fail_before_inference(self) -> None:
        for data in ({"prompt": ""}, {"prompt": []}, {"prompt": "x" * 2001}, {"prompt": "hello", "tools": []}):
            status, _ = await self.post("chat", data)
            self.assertEqual(status, 400)

    async def test_voice_requires_consent_and_returns_private_audio_without_key(self) -> None:
        await self.services.voice.stop()
        calls = []
        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            return httpx.Response(200, content=b"ID3-private-audio", headers={"content-type": "audio/mpeg"})
        self.services.voice = ElevenLabsVoice(self.audit, VoiceConfig(
            enabled=True, api_key="test-only-key", voice_id="testVoiceID"), transport=httpx.MockTransport(handler))
        for data in ({"text": "hello", "cloud_consent": False}, {"text": "hello", "cloud_consent": 1},
                     {"text": [], "cloud_consent": True}, {"text": "hello", "cloud_consent": True, "voice_id": "other"}):
            status, _ = await self.post("voice/speech", data)
            self.assertEqual(status, 400)
        self.assertFalse(calls)
        status, raw, headers = await self.request("/api/v1/voice/speech/", method="POST",
                                                 body=json.dumps({"text": "hello", "cloud_consent": True}).encode(), token="test-token")
        self.assertEqual(status, 200)
        self.assertEqual(raw, b"ID3-private-audio")
        self.assertEqual(headers[b"cache-control"], b"no-store")
        self.assertEqual(headers[b"content-type"], b"audio/mpeg")
        self.assertEqual(len(headers[b"x-thorn-speech-id"]), 32)
        self.assertFalse(self.executed)
