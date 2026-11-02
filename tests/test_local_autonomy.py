"""Garantias locales: expiracion, permisos, DPAPI y trabajo nativo fuera del loop."""
from __future__ import annotations

import asyncio
import io
import json
import os
import tempfile
import threading
import unittest
import wave
from pathlib import Path
from unittest.mock import Mock, patch

from core.local_voice import LocalVoice, MAX_AUDIO, BoundedAudio
from core.sessions import SessionAuthority, authorize
from thorn_web.credentials import dpapi, root_credential
from tests.test_assistant import MemoryAudit


def wav_fixture() -> bytes:
    output = io.BytesIO()
    with wave.open(output, 'wb') as stream:
        stream.setnchannels(1); stream.setsampwidth(2); stream.setframerate(22050)
        stream.writeframes(b'\0' * 2205 * 2)
    return output.getvalue()


class SessionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = 0.0
        self.authority = SessionAuthority(clock=lambda: self.now, ttl=300, absolute_ttl=600)

    def test_random_tokens_only_hashes_retained(self) -> None:
        first, second = self.authority.issue(), self.authority.issue()
        self.assertNotEqual(first['access_token'], second['access_token'])
        self.assertNotIn(first['access_token'], repr(self.authority.sessions))
        self.assertEqual(len(first['access_token']), 68)

    def test_rotation_grace_replay_absolute_expiry_and_revoke(self) -> None:
        first = self.authority.issue()['access_token']
        self.now = 250
        second = self.authority.renew(first)['access_token']
        self.assertIsNotNone(self.authority.find(first))
        with self.assertRaisesRegex(ValueError, 'invalid_session'):
            self.authority.renew(first)
        self.now = 281
        self.assertIsNone(self.authority.find(first))
        self.now = 500
        third = self.authority.renew(second)
        self.assertEqual(third['expires_in'], 100)
        self.now = 600
        self.assertIsNone(self.authority.find(third['access_token']))
        new = self.authority.issue()['access_token']
        rotated = self.authority.renew(new)['access_token']
        self.assertTrue(self.authority.revoke(new))
        self.assertIsNone(self.authority.find(rotated))
        payload = self.authority.issue()
        new_token = self.authority.renew(payload['access_token'])['access_token']
        self.authority.revoke_identity(payload['session_id'])
        self.assertIsNone(self.authority.find(new_token))

    def test_expiry_limits_closed_and_no_privilege_escalation(self) -> None:
        authority = SessionAuthority(clock=lambda: self.now, max_sessions=1)
        first = authority.issue()['access_token']
        with self.assertRaisesRegex(RuntimeError, 'session_limit'):
            authority.issue()
        self.now = 901
        self.assertIsNone(authority.find(first))
        authority.issue()
        with self.assertRaises(ValueError):
            authority.issue(frozenset({'shell:execute'}))
        authority.close(); self.assertFalse(authority.sessions)
        with self.assertRaises(ValueError): authority.issue()

    def test_scope_duplicate_authorization_and_owner_compatibility(self) -> None:
        token = self.authority.issue(frozenset({'telemetry:read'}))['access_token']
        headers = [(b'authorization', ('Bearer ' + token).encode())]
        def check(path: str, values: list | None = None) -> int | None:
            return authorize(headers if values is None else values, root='owner-only',
                authority=self.authority, path=path, method='POST')
        self.assertIsNone(check('/api/v1/system/'))
        self.assertEqual(check('/api/v1/microphone/transcribe/'), 403)
        self.assertEqual(check('/api/v1/actions/confirm/'), 403)
        self.assertEqual(check('/api/v1/voice/speech/'), 403)
        self.assertEqual(check('/api/v1/system/', headers * 2), 401)
        self.assertEqual(check('/api/v1/system/', []), 401)
        self.assertIsNone(check('/api/v1/chat/', [(b'authorization', b'Bearer owner-only')]))


@unittest.skipUnless(os.name == 'nt', 'DPAPI requiere Windows')
class CredentialTests(unittest.TestCase):
    def test_dpapi_roundtrip_entropy_and_tampering(self) -> None:
        original = b'fake-secret-for-tests-only'
        encrypted = dpapi(original, b'test-entropy')
        self.assertNotIn(original, encrypted)
        self.assertEqual(dpapi(encrypted, b'test-entropy', decrypt=True), original)
        with self.assertRaises(OSError): dpapi(encrypted, b'other', decrypt=True)
        with self.assertRaises(OSError): dpapi(encrypted[:30], b'test-entropy', decrypt=True)

    def test_migration_preserves_authority_and_fails_closed_on_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); secret = 'test-only-' + 'a' * 48
            self.assertEqual(root_credential(root, secret), secret)
            self.assertEqual(root_credential(root), secret)
            self.assertNotIn(secret.encode(), (root/'data/security/authority.dpapi').read_bytes())
            with self.assertRaisesRegex(OSError, 'conflict'):
                root_credential(root, 'b' * 48)
            (root/'data/security/authority.dpapi').write_bytes(b'corrupt')
            with self.assertRaisesRegex(OSError, 'invalid_credential_store'):
                root_credential(root)


class LocalVoiceTests(unittest.IsolatedAsyncioTestCase):
    async def make_voice(self, **kwargs: object) -> LocalVoice:
        voice = LocalVoice(MemoryAudit(), enabled=True, loader=lambda path: object(), **kwargs)
        self.addAsyncCleanup(voice.stop); await voice.start(); return voice

    async def test_local_audio_contract_and_private_audit(self) -> None:
        voice = await self.make_voice(synthesizer=lambda model, text: wav_fixture())
        self.assertFalse(voice.status['cloud']); self.assertTrue(voice.status['can_request'])
        result = await voice.speak('private-text', cloud_consent=False, request_id='test')
        with wave.open(io.BytesIO(result)) as stream:
            self.assertEqual(stream.getframerate(), 22050)
        self.assertNotIn('private-text', json.dumps(voice.audit.rows))
        with self.assertRaises(ValueError):
            await voice.speak('hello', cloud_consent=True, request_id='test')

    async def test_cancellation_keeps_lock_until_native_worker_releases(self) -> None:
        entered, finish = threading.Event(), threading.Event()
        def blocking(model: object, text: str) -> bytes:
            entered.set(); finish.wait(3); return wav_fixture()
        voice = await self.make_voice(synthesizer=blocking)
        task = asyncio.create_task(voice.speak('test', cloud_consent=False, request_id='cancel'))
        try:
            while not entered.is_set(): await asyncio.sleep(0.005)
            task.cancel(); await asyncio.sleep(0.02)
            self.assertTrue(voice.status['busy']); self.assertFalse(task.done())
            with self.assertRaisesRegex(RuntimeError, 'voice_busy'):
                await voice.speak('second', cloud_consent=False, request_id='two')
        finally:
            finish.set()
        with self.assertRaises(asyncio.CancelledError): await task
        self.assertFalse(voice.status['busy'])
        self.assertEqual(voice.audit.rows[-1]['status'], 'cancelled')

    async def test_invalid_output_and_unbounded_audio_are_rejected(self) -> None:
        voice = await self.make_voice(synthesizer=lambda model, text: b'not-a-wave')
        with self.assertRaisesRegex(RuntimeError, 'voice_synthesis_failed'):
            await voice.speak('test', cloud_consent=False, request_id='test')
        with BoundedAudio() as output:
            output.seek(MAX_AUDIO)
            with self.assertRaises(ValueError): output.write(b'x')

    async def test_policy_blocks_paid_voice_and_search_before_client_creation(self) -> None:
        from core.runtime import ThornRuntime
        from core.services import AssistantServices
        with patch.dict(os.environ, {'THORN_LOCAL_AI_ONLY': '1', 'THORN_VOICE_PROVIDER': 'elevenlabs',
                                     'THORN_SEARCH_PROVIDER': 'brave', 'THORN_INTERNET_ENABLED': '1'}), \
             patch('core.services.ElevenLabsVoice', side_effect=AssertionError('paid client created')):
            services = AssistantServices(ThornRuntime(use_database=False), database=False, sensor_interval=2)
            try:
                self.assertEqual(services.voice.status['state'], 'policy_blocked')
                self.assertFalse(services.internet.status['search_available'])
                with self.assertRaisesRegex(RuntimeError, 'web_paid_provider_blocked'):
                    await services.internet.search('test', consent=True, identity='test')
            finally: await services.stop()
