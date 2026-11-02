"""Aislamiento y operaciones Bastion 9: sin efectos Windows ni datos personales."""

from __future__ import annotations

import asyncio
import io
import json
import unittest
from unittest.mock import AsyncMock
from unittest.mock import patch
import sys

import httpx
from pypdf import PdfWriter

from core.commands import command_intent
from core.document_worker import parse_pdf
from core.documents import DocumentService, parse_isolated, question_excerpts
from core.interaction import InteractionStore
from core.local_llm import LocalLanguageModel
from core.sessions import SessionAuthority
from core.maps import find_places
from core.internet import InternetService
from core.event_bus import EventBus
from tests.test_network import FakeWebClient
from tests.test_assistant import MemoryAudit
from tests import test_assistant_api as assistant_api


def blank_pdf() -> bytes:
    output = io.BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=300, height=300)
    writer.write(output)
    return output.getvalue()


class InteractionTests(unittest.IsolatedAsyncioTestCase):
    async def test_pdf_question_can_select_later_pages_with_a_bounded_prompt(self) -> None:
        pages = [{"page": 1, "text": "Introduccion " * 200},
                 {"page": 2, "text": "Indice " * 200},
                 {"page": 3, "text": "El fusible F9 protege el circuito de alimentacion." * 50}]
        excerpts = question_excerpts(pages, "Que fusible protege la alimentacion?")
        self.assertIn(3, [page["page"] for page in excerpts])
        self.assertLessEqual(sum(len(page["text"]) for page in excerpts), 3600)
        self.assertIn("fusible F9", next(page["text"] for page in excerpts if page["page"] == 3))

    async def test_expiry_and_revocation_release_context_and_panels(self) -> None:
        now = [0.0]
        store = InteractionStore(clock=lambda: now[0], ttl=10)
        authority = SessionAuthority(clock=lambda: now[0], ttl=60)
        authority.on_release(store.release)
        token = authority.issue()['access_token']
        owner = authority.find(token).identity
        state = store.state(owner)
        store.remember(owner, state, 'nombre privado', 'respuesta privada')
        store.add_panel(owner, 'answer', 'Respuesta', {'text': 'privado'})
        self.assertTrue(authority.revoke(token))
        self.assertNotIn(owner, store.states)
        state = store.state('another')
        store.remember('another', state, 'hola', 'hola')
        now[0] = 11
        self.assertEqual(store.snapshot('another')['context_messages'], 0)

    async def test_panels_are_bounded_owned_and_positions_finite(self) -> None:
        store = InteractionStore()
        for index in range(10):
            store.add_panel('a', 'answer', str(index), {'text': 'dato real'})
        first = store.snapshot('a')['panels'][0]
        self.assertEqual(len(store.snapshot('a')['panels']), 8)
        with self.assertRaisesRegex(ValueError, 'panel_not_found'):
            store.update_panel('b', first['id'], 'close')
        for x in (True, float('nan'), 1.1, -0.1):
            with self.assertRaisesRegex(ValueError, 'invalid_panel_position'):
                store.update_panel('a', first['id'], 'move', {'x': x, 'y': 0.5})
        updated = store.update_panel('a', first['id'], 'minimize')
        self.assertEqual(updated['panels'][-1]['state'], 'minimized')

    async def test_pdf_parser_isolated_and_no_ocr_claim(self) -> None:
        raw = blank_pdf()
        result = await parse_isolated(raw)
        self.assertEqual(result['page_count'], 1)
        self.assertFalse(result['text_available'])
        self.assertFalse(result['ocr'])
        with self.assertRaisesRegex(ValueError, 'document_invalid_pdf'):
            await parse_isolated(b'not a PDF')
        writer = PdfWriter()
        writer.add_blank_page(width=300, height=300)
        writer.encrypt('test-only')
        stream = io.BytesIO(); writer.write(stream)
        with self.assertRaisesRegex(ValueError, 'document_encrypted'):
            parse_pdf(stream.getvalue())

    async def test_document_ownership_and_abandoned_upload(self) -> None:
        service = DocumentService()
        record = await service.upload('a', blank_pdf(), 'prueba.pdf')
        with self.assertRaisesRegex(ValueError, 'document_not_found'):
            service.get('b', record['id'])
        service.release('a')
        self.assertFalse(service.items)
        with self.assertRaisesRegex(RuntimeError, 'interaction_cancelled'):
            await service.upload('a', blank_pdf(), 'prueba.pdf', alive=lambda: False)
        self.assertFalse(service.items)

    async def test_commands_match_whole_requests(self) -> None:
        self.assertEqual(command_intent('T.H.O.R.N, muestra el sistema, por favor')['view'], 'system')
        self.assertIsNone(command_intent('Explicame que significa "muestra el sistema"'))
        self.assertEqual(command_intent('pausa la musica')['action_id'], 'media_play_pause')
        self.assertEqual(command_intent('deten la musica')['action_id'], 'media_stop')

    async def test_pdf_cancellation_terminates_its_owned_process(self) -> None:
        spawn = asyncio.create_subprocess_exec
        ready = asyncio.Event()
        children = []
        async def fake_parser(*args, **options):
            process = await spawn(sys.executable, '-c', 'import sys,time;sys.stdin.buffer.read();time.sleep(60)', **options)
            children.append(process); ready.set(); return process
        with patch('core.documents.asyncio.create_subprocess_exec', side_effect=fake_parser):
            task = asyncio.create_task(parse_isolated(blank_pdf()))
            await asyncio.wait_for(ready.wait(), 2)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertIsNotNone(children[0].returncode)

    async def test_maps_require_consent_and_never_invent_coordinates(self) -> None:
        bus = EventBus(); audit = MemoryAudit()
        client = FakeWebClient({'query': {'pages': [{'title': 'Londres', 'index': 1,
            'fullurl': 'https://es.wikipedia.org/wiki/Londres', 'coordinates': [{'lat': 51.5, 'lon': -0.12}]}]}})
        service = InternetService(audit, bus, enabled=True, provider='wikipedia', client=client)
        self.addAsyncCleanup(service.stop)
        with self.assertRaisesRegex(ValueError, 'web_internet_consent_required'):
            await find_places(service, 'Londres', consent=False, request_id='test')
        self.assertFalse(client.calls)
        result = await find_places(service, 'Londres', consent=True, request_id='test')
        self.assertEqual(result['places'][0]['latitude'], 51.5)
        self.assertIn('openstreetmap.org', result['places'][0]['map_url'])


class Bastion9APITests(unittest.IsolatedAsyncioTestCase):
    request = assistant_api.AssistantAPITests.request
    asyncSetUp = assistant_api.AssistantAPITests.asyncSetUp
    asyncTearDown = assistant_api.AssistantAPITests.asyncTearDown

    async def call(self, route: str, data: dict[str, object] | None = None, token: str = 'test-token'):
        status, raw, _ = await self.request('/api/v1/' + route + '/', token=token,
            method='GET' if data is None else 'POST', body=b'' if data is None else json.dumps(data).encode())
        return status, json.loads(raw)

    async def fake_model(self) -> list[list[dict[str, str]]]:
        await self.services.language.stop()
        received = []
        def respond(request: httpx.Request) -> httpx.Response:
            if request.url.path == '/api/tags':
                return httpx.Response(200, json={'models': [{'name': 'thorn:test'}]})
            data = json.loads(request.content)
            received.append(data['messages'])
            return httpx.Response(200, json={'done': True, 'message': {'content': json.dumps({'reply': 'Respuesta local valida', 'suggested_action': None})}})
        self.services.language = LocalLanguageModel(self.audit, endpoint='http://127.0.0.1:11434',
            model='thorn:test', transport=httpx.MockTransport(respond))
        return received

    async def test_history_survives_rotation_but_never_crosses_sessions(self) -> None:
        received = await self.fake_model()
        first = self.runtime.sessions.issue()['access_token']
        second = self.runtime.sessions.issue()['access_token']
        self.assertEqual((await self.call('chat', {'prompt': 'Mi proyecto es prueba'}, first))[0], 200)
        self.assertEqual((await self.call('chat', {'prompt': 'Cual era mi proyecto'}, first))[0], 200)
        self.assertEqual(len(received[-1]), 4)
        self.assertEqual((await self.call('chat', {'prompt': 'Hola'}, second))[0], 200)
        self.assertEqual(len(received[-1]), 2)
        rotated = self.runtime.sessions.renew(first)['access_token']
        await self.call('chat', {'prompt': 'Continua por favor'}, rotated)
        self.assertEqual(len(received[-1]), 6)
        owner = self.runtime.sessions.find(rotated).identity
        self.runtime.sessions.revoke(rotated)
        self.assertNotIn(owner, self.services.operator.store.states)

    async def test_fast_commands_and_briefing_need_no_model_or_network(self) -> None:
        self.services.language.ask = AsyncMock(side_effect=AssertionError('LLM inesperado'))
        status, result = await self.call('chat', {'prompt': 'muestra el sistema'})
        self.assertEqual(status, 200)
        self.assertEqual(result['ui_commands'], [{'type': 'navigate', 'view': 'system'}])
        status, result = await self.call('briefing')
        self.assertEqual(status, 200)
        self.assertFalse(result['panel']['data']['external_requests'])
        self.assertFalse(result['panel']['data']['telemetry_fresh'])
        self.assertIn('no hay una muestra vigente', result['reply'])

    async def test_cancelled_ignored_model_cannot_restore_erased_context(self) -> None:
        started = asyncio.Event()
        async def delayed(*args, **kwargs):
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                return {'reply': 'respuesta tardia', 'suggested_action': None}
        self.services.language.ask = delayed
        token = self.runtime.sessions.issue()['access_token']
        pending = asyncio.create_task(self.call('chat', {'prompt': 'pregunta lenta'}, token))
        await asyncio.wait_for(started.wait(), 2)
        status, _ = await self.call('conversation/reset', {}, token)
        self.assertEqual(status, 200)
        status, result = await pending
        self.assertEqual(result['error'], 'interaction_cancelled')
        status, state = await self.call('workspace', token=token)
        self.assertEqual(state['context_messages'], 0)
        self.assertFalse(state['panels'])

    async def test_documents_are_authenticated_before_buffering_and_are_private(self) -> None:
        raw = blank_pdf()
        code, _, _ = await self.request('/api/v1/documents/upload/', method='POST', body=raw, content_type='application/pdf')
        self.assertEqual(code, 401)
        token = self.runtime.sessions.issue()['access_token']
        code, result, _ = await self.request('/api/v1/documents/upload/', method='POST', body=raw,
            token=token, content_type='application/pdf', extra_headers=[(b'x-thorn-document-consent', b'local')])
        self.assertEqual(code, 201)
        identity = json.loads(result)['document']['id']
        other = self.runtime.sessions.issue()['access_token']
        status, _ = await self.call(f'documents/{identity}/download', token=other)
        self.assertEqual(status, 400)
        self.runtime.sessions.revoke(token)
        self.assertFalse(self.services.operator.documents.items)

    async def test_new_scopes_and_no_automatic_multimedia_execution(self) -> None:
        restricted = self.runtime.sessions.issue(frozenset({'telemetry:read'}))['access_token']
        for route in ('workspace', 'preferences'):
            self.assertEqual((await self.call(route, token=restricted))[0], 403)
        status, result = await self.call('chat', {'prompt': 'deten la musica'})
        self.assertEqual(status, 200)
        self.assertEqual(result['suggested_action'], 'media_stop')
        self.assertTrue(result['requires_confirmation'])
        self.assertFalse(self.executed)

    async def test_large_pdf_stays_in_memory_and_does_not_relax_json_limits(self) -> None:
        import tempfile
        output = io.BytesIO(); writer = PdfWriter()
        writer.add_blank_page(width=300, height=300)
        writer.add_attachment('fixture.txt', b'x' * 600000)
        writer.write(output)
        spool = tempfile.SpooledTemporaryFile
        buffers = []
        def observe_buffer(*args, **kwargs):
            value = spool(*args, **kwargs); buffers.append(value); return value
        with patch('django.core.handlers.asgi.tempfile.SpooledTemporaryFile', side_effect=observe_buffer):
            code, _, _ = await self.request('/api/v1/documents/upload/', method='POST', body=output.getvalue(),
                token='test-token', content_type='application/pdf', extra_headers=[(b'x-thorn-document-consent', b'local')])
        self.assertEqual(code, 201)
        self.assertTrue(buffers)
        self.assertTrue(all(not buffer._rolled for buffer in buffers))
        code, _, _ = await self.request('/api/v1/chat/', method='POST', body=b' ' * 20000, token='test-token')
        self.assertEqual(code, 413)

    async def test_second_pdf_is_rejected_before_receiving_its_bytes(self) -> None:
        started, release = asyncio.Event(), asyncio.Event()
        consumed = []
        async def waiting_app(scope, receive, send):
            await receive(); started.set(); await release.wait()
        app = assistant_api.ThornASGI(waiting_app, lambda: self.runtime)
        app._runtime = self.runtime
        scope = {'type': 'http', 'path': '/api/v1/documents/upload/', 'method': 'POST',
                 'headers': [(b'authorization', b'Bearer test-token')]}
        async def receive():
            consumed.append(True); return {'type': 'http.request', 'body': b'pdf'}
        messages = []
        async def send(message):
            messages.append(message)
        pending = asyncio.create_task(app(scope, receive, send))
        try:
            await asyncio.wait_for(started.wait(), 2)
            await app(scope, receive, send)
            self.assertEqual(len(consumed), 1)
            self.assertEqual(messages[0]['status'], 503)
        finally:
            release.set(); await pending

    async def test_client_cannot_supply_system_messages_or_another_session_history(self) -> None:
        for field in ('history', 'messages', 'owner'):
            status, value = await self.call('chat', {'prompt': 'hola', field: 'otro contexto'})
            self.assertEqual(status, 400)
            self.assertEqual(value['error'], 'invalid_fields')

    async def test_preferences_are_explicit_and_do_not_accept_permissions(self) -> None:
        self.services.operator.preferences.audit = self.audit
        value = {'display_name': 'Operador de prueba', 'briefing_enabled': False,
                 'memory_context_enabled': False, 'timezone': 'America/Santiago'}
        status, _ = await self.call('preferences/save', {'preferences': {**value, 'camera_consent': True}})
        self.assertEqual(status, 400)
        self.assertFalse(self.audit.settings)
        status, _ = await self.call('preferences/save', {'preferences': value})
        self.assertEqual(status, 200)
        self.assertEqual(self.audit.settings['operator_preferences'], value)
        status, result = await self.call('preferences')
        self.assertEqual(result['preferences']['display_name'], 'Operador de prueba')
