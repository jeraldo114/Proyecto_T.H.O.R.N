"""Voz con transporte simulado: ninguna prueba llama a ElevenLabs o consume saldo."""

from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from core.elevenlabs_voice import ElevenLabsVoice, MAX_AUDIO, VoiceConfig
from core.personality import PERSONALITY, VOICE_DESCRIPTION, VOICE_PREVIEW, system_prompt
from tests.test_assistant import MemoryAudit
from tools.elevenlabs_voice import provider_error, provider_json, write_previews


class VoiceTests(unittest.IsolatedAsyncioTestCase):
    async def make_voice(self, handler: object, config: VoiceConfig | None = None) -> ElevenLabsVoice:
        voice = ElevenLabsVoice(MemoryAudit(), config or VoiceConfig(
            enabled=True, api_key="test-only-key", voice_id="testVoiceID"),
            transport=httpx.MockTransport(handler))
        self.addAsyncCleanup(voice.stop)
        return voice

    async def test_no_consent_or_invalid_text_never_contacts_provider(self) -> None:
        handler = unittest.mock.Mock(side_effect=AssertionError("no external request"))
        voice = await self.make_voice(handler)
        for consent in (False, "true", 1, None):
            with self.assertRaisesRegex(ValueError, "voice_cloud_consent_required"):
                await voice.speak("hello", cloud_consent=consent, request_id="test")
        for text in ("", " ", "x" * 2001, []):
            with self.assertRaisesRegex(ValueError, "voice_invalid_text"):
                await voice.speak(text, cloud_consent=True, request_id="test")
        handler.assert_not_called()
        self.assertFalse(voice.audit.rows)

    async def test_disabled_missing_key_missing_voice_and_closed_do_not_send(self) -> None:
        handler = unittest.mock.Mock(side_effect=AssertionError("no external request"))
        for config, state in ((VoiceConfig(), "disabled"),
                              (VoiceConfig(enabled=True), "missing_key"),
                              (VoiceConfig(enabled=True, api_key="test-only-key"), "missing_voice")):
            voice = await self.make_voice(handler, config)
            self.assertEqual(voice.status["state"], state)
            with self.assertRaisesRegex(RuntimeError, "voice_" + state):
                await voice.speak("hello", cloud_consent=True, request_id="test")
        await voice.stop()
        with self.assertRaisesRegex(RuntimeError, "voice_closed"):
            await voice.speak("hello", cloud_consent=True, request_id="test")
        handler.assert_not_called()

    async def test_real_contract_and_audit_exclude_text_and_key(self) -> None:
        requests = []
        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(200, content=b"ID3-test-payload", headers={"content-type": "audio/mpeg"})
        voice = await self.make_voice(handler)
        self.assertEqual(voice.status["state"], "configured")
        self.assertFalse(requests, "status must not perform I/O")
        audio = await voice.speak("Confidential test text", cloud_consent=True, request_id="test")
        self.assertEqual(audio, b"ID3-test-payload")
        self.assertEqual(len(requests), 1)
        request = requests[0]
        self.assertEqual(request.url.host, "api.elevenlabs.io")
        self.assertEqual(request.url.scheme, "https")
        self.assertEqual(request.url.path, "/v1/text-to-speech/testVoiceID/stream")
        self.assertEqual(request.headers["xi-api-key"], "test-only-key")
        payload = json.loads(request.content)
        self.assertEqual(payload["language_code"], "es")
        self.assertEqual(payload["model_id"], "eleven_flash_v2_5")
        self.assertNotIn("Confidential test text", json.dumps(voice.audit.rows))
        self.assertNotIn("test-only-key", json.dumps(voice.status))
        self.assertNotIn("test-only-key", repr(voice.config))
        self.assertEqual(voice.status["state"], "ready")
        self.assertEqual([row["status"] for row in voice.audit.rows], ["requested", "synthesized"])

    async def test_multilingual_omits_unsupported_language_parameter(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            self.assertNotIn("language_code", json.loads(request.content))
            return httpx.Response(200, content=b"ID3", headers={"content-type": "audio/mpeg"})
        voice = await self.make_voice(handler, VoiceConfig(enabled=True, api_key="test-only-key",
                                                            voice_id="test", model="eleven_multilingual_v2"))
        await voice.speak("hello", cloud_consent=True, request_id="test")

    async def test_http_errors_are_redacted_and_never_retried(self) -> None:
        for status, expected in ((401, "auth_failed"), (403, "auth_failed"), (404, "not_found"),
                                 (429, "rate_limited"), (500, "provider_unavailable"),
                                 (302, "provider_unavailable")):
            calls = []
            def handler(request: httpx.Request) -> httpx.Response:
                calls.append(request)
                return httpx.Response(status, text="private vendor text", headers={"Location": "https://other.invalid"})
            voice = await self.make_voice(handler)
            with self.assertRaisesRegex(RuntimeError, "^voice_" + expected + "$"):
                await voice.speak("hello", cloud_consent=True, request_id="test")
            self.assertEqual(len(calls), 1)
            self.assertEqual(voice.status["state"], expected)

    async def test_network_error_is_safe(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("private token in error", request=request)
        voice = await self.make_voice(handler)
        with self.assertRaisesRegex(RuntimeError, "^voice_provider_unavailable$"):
            await voice.speak("hello", cloud_consent=True, request_id="test")

    async def test_audio_empty_wrong_mime_and_oversized_fail_closed(self) -> None:
        for raw, mime in ((b"", "audio/mpeg"), (b"{}", "application/json"),
                          (b"x" * (MAX_AUDIO + 1), "audio/mpeg")):
            voice = await self.make_voice(lambda _: httpx.Response(200, content=raw, headers={"content-type": mime}))
            with self.assertRaisesRegex(RuntimeError, "voice_invalid_audio"):
                await voice.speak("hello", cloud_consent=True, request_id="test")
            self.assertFalse(voice.status["busy"])

    async def test_busy_cancel_and_resource_closure(self) -> None:
        started = asyncio.Event()
        async def handler(request: httpx.Request) -> httpx.Response:
            started.set()
            await asyncio.Event().wait()
            return httpx.Response(200)
        voice = await self.make_voice(handler)
        task = asyncio.create_task(voice.speak("hello", cloud_consent=True, request_id="first"))
        await started.wait()
        with self.assertRaisesRegex(RuntimeError, "voice_busy"):
            await voice.speak("second", cloud_consent=True, request_id="second")
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(voice.audit.rows[-1]["status"], "cancelled")
        self.assertFalse(voice.status["busy"])
        await voice.stop()
        self.assertTrue(voice.client.is_closed)

    async def test_stop_waits_for_request_and_rejects_new_ones(self) -> None:
        started, release = asyncio.Event(), asyncio.Event()
        async def handler(request: httpx.Request) -> httpx.Response:
            started.set()
            await release.wait()
            return httpx.Response(200, content=b"ID3", headers={"content-type": "audio/mpeg"})
        voice = await self.make_voice(handler)
        task = asyncio.create_task(voice.speak("hello", cloud_consent=True, request_id="first"))
        await started.wait()
        stop = asyncio.create_task(voice.stop())
        await asyncio.sleep(0)
        self.assertFalse(stop.done())
        release.set()
        await task
        await stop
        self.assertTrue(voice.client.is_closed)
        self.assertFalse(voice.status["can_request"])

    async def test_audit_failure_does_not_leak_or_prevent_speech(self) -> None:
        voice = await self.make_voice(lambda _: httpx.Response(200, content=b"ID3", headers={"content-type": "audio/mpeg"}))
        voice.audit.append = AsyncMock(side_effect=RuntimeError("private DB text"))
        await voice.speak("hello", cloud_consent=True, request_id="test")
        self.assertEqual(voice.status["audit_errors"], 2)

    async def test_design_http_uses_fixed_origin_and_does_not_follow_redirect(self) -> None:
        async with httpx.AsyncClient(base_url="https://api.elevenlabs.io", follow_redirects=False,
                                     transport=httpx.MockTransport(lambda _: httpx.Response(302))) as client:
            with self.assertRaisesRegex(RuntimeError, "HTTP 302"):
                await provider_json(client, "POST", "/v1/text-to-voice/design", key="test-only-key")

    async def test_design_rejection_is_classified_without_retries_or_private_text(self) -> None:
        calls = []
        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            return httpx.Response(403, json={"detail": {"status": "feature_not_available",
                "message": "A paid plan is required. confidential-value-must-not-appear"}})
        async with httpx.AsyncClient(base_url="https://api.elevenlabs.io",
                                     transport=httpx.MockTransport(handler)) as client:
            with self.assertRaisesRegex(RuntimeError, "paid_plan_required") as error:
                await provider_json(client, "POST", "/v1/text-to-voice/design", key="test-only-key")
            self.assertNotIn("confidential-value", str(error.exception))
            self.assertEqual(len(calls), 1)


class VoiceConfigTests(unittest.TestCase):
    def test_error_messages_and_unknown_status_are_never_exposed(self) -> None:
        for data in ({"detail": {"status": "private-test-value", "message": "private-test-value"}},
                     {"detail": "private-test-value"}, {"detail": []}, None):
            message = provider_error(403, data)
            self.assertNotIn("private-test-value", message)
            self.assertIn("unspecified", message)
        self.assertIn("missing_permissions", provider_error(401, {"detail": {"status": "missing_permissions"}}))

    def test_path_and_header_injection_are_rejected(self) -> None:
        for identity in ("../voice", "https://other.invalid", "voice?key=x", "x\r\nOther:y"):
            with self.assertRaises(ValueError):
                VoiceConfig(voice_id=identity)
        with self.assertRaises(ValueError):
            VoiceConfig(api_key="key\r\nInjected:1")
        with self.assertRaises(ValueError):
            VoiceConfig(model="not_allowed")

    def test_invalid_optional_environment_degrades_without_breaking_runtime(self) -> None:
        with patch.dict("os.environ", {"THORN_VOICE_ENABLED": "oops"}):
            self.assertTrue(VoiceConfig.from_env().invalid)

    def test_personality_retains_truth_permissions_and_original_identity(self) -> None:
        prompt = system_prompt({"telemetry_fresh": False})
        for fragment in ("confirmacion humana independiente", "no hay lectura vigente",
                         "No inventes", "JSON", "no eres J.A.R.V.I.S", "humor seco"):
            self.assertIn(fragment, prompt)
        self.assertIn(PERSONALITY, prompt)
        self.assertTrue(20 <= len(VOICE_DESCRIPTION) <= 1000)
        self.assertTrue(100 <= len(VOICE_PREVIEW) <= 1000)

    def test_generated_files_are_private_and_not_overwritten(self) -> None:
        data = {"previews": [{"generated_voice_id": "testID", "audio_base_64": base64.b64encode(b"ID3").decode(), "media_type": "audio/mpeg"}]}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first, second = write_previews(data, root=root), write_previews(data, root=root)
            self.assertNotEqual(first, second)
            self.assertEqual(first.parent, root / "data" / "voice-design")
            self.assertEqual((first / "preview-1.mp3").read_bytes(), b"ID3")
            self.assertNotIn("audio_base_64", (first / "previews.json").read_text())

    def test_invalid_design_previews_do_not_create_files(self) -> None:
        for data in ({}, {"previews": []}, {"previews": [{"generated_voice_id": "../escape"}]},
                     {"previews": [{"generated_voice_id": "test", "audio_base_64": "!!!"}]}):
            with tempfile.TemporaryDirectory() as directory:
                with self.assertRaises(ValueError):
                    write_previews(data, root=Path(directory))
                self.assertEqual(list(Path(directory).iterdir()), [])
