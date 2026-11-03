"""Memoria durable: cifrado real, permisos, recuperación y borrado sin resurrección."""
from __future__ import annotations

import json
import os
import unittest
from contextlib import asynccontextmanager
from datetime import datetime
from unittest.mock import AsyncMock, Mock, patch

from core.audit import AuditStore
from core.conversation_memory import ConversationMemory, words
from core.secure_storage import StorageCipher
from core.sessions import required_scope
from core.documents import DocumentService, question_excerpts
from tests.test_bastion9 import blank_pdf


class ConversationMemoryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.cipher = StorageCipher(os.urandom(32))
        self.audit = AuditStore(enabled=True, cipher=self.cipher)
        self.memory = ConversationMemory(self.audit)
        self.memory.ready = True
        self.conn = Mock(commit=AsyncMock())
        self.cur = Mock(execute=AsyncMock(), executemany=AsyncMock(), fetchone=AsyncMock(),
                        fetchall=AsyncMock(), lastrowid=7, rowcount=3)
        self.cur.fetchone.return_value = (self.cipher.encrypt(0, "setting:conversation_epoch"),)
        @asynccontextmanager
        async def session():
            yield self.conn, self.cur
        self.patch = patch("core.conversation_memory.db_session", session)
        self.patch.start(); self.addCleanup(self.patch.stop)

    async def test_concept_query_matches_old_index_without_reindex(self) -> None:
        old_terms = set(self.memory.term_hashes("Proyecto con cámaras"))
        new_terms = set(self.memory.term_hashes("Mi idea para vigilar la casa", 24, related=True))
        self.assertTrue(old_terms & new_terms)
        self.assertFalse(old_terms & set(self.memory.term_hashes("Mi idea para vigilar la casa")))

    async def test_paused_recording_does_not_insert(self) -> None:
        self.cur.fetchone.side_effect = [self.cur.fetchone.return_value,
                                       (self.cipher.encrypt(True, "setting:conversation_paused"),)]
        self.assertFalse(await self.memory.append("Antes", "Después", source="local_llm", epoch=0))
        self.conn.commit.assert_not_awaited()

    async def test_full_turn_is_encrypted_and_index_cannot_reveal_words(self) -> None:
        prompt, reply = "Mi prototipo se llama espino azul", "Guardaré ese nombre: espino azul."
        self.cur.fetchone.side_effect = [self.cur.fetchone.return_value, None]
        self.assertTrue(await self.memory.append(prompt, reply, source="local_llm", epoch=0))
        calls = self.cur.execute.call_args_list
        self.assertIn("FOR UPDATE", calls[0].args[0])
        identity, _, source, encoded = calls[2].args[1]
        self.assertEqual(self.cipher.decrypt(encoded, f"conversation:{identity}"), {"prompt": prompt, "reply": reply})
        self.assertNotIn("espino", str(calls))
        self.assertTrue(all(len(bytes.fromhex(term)) == 32 for term, _ in self.cur.executemany.call_args.args[1]))
        self.conn.commit.assert_awaited_once()

    async def test_new_instance_recovers_full_text_without_session_state(self) -> None:
        identity = "a" * 32
        original = {"prompt": "un recuerdo " + "x" * 1900, "reply": "respuesta " + "y" * 3900}
        self.cur.fetchall.return_value = [(7, identity, datetime(2026, 11, 2), "local_llm",
                                          self.cipher.encrypt(original, f"conversation:{identity}"))]
        restarted = ConversationMemory(self.audit); restarted.ready = True
        result = await restarted.page()
        self.assertEqual(result["records"][0]["prompt"], original["prompt"])
        self.assertEqual(result["records"][0]["reply"], original["reply"])
        self.assertTrue(result["durable"])

    async def test_delete_epoch_prevents_late_turn_from_returning(self) -> None:
        self.cur.fetchone.return_value = (self.cipher.encrypt(1, "setting:conversation_epoch"),)
        self.assertFalse(await self.memory.append("antes", "después", source="local_llm", epoch=0))
        self.assertEqual(self.cur.execute.await_count, 1)
        self.conn.commit.assert_not_awaited()

    async def test_no_database_or_cipher_never_writes_plaintext(self) -> None:
        for audit in (AuditStore(enabled=False, cipher=self.cipher), AuditStore(enabled=True)):
            memory = ConversationMemory(audit); memory.ready = True
            self.assertFalse(memory.available)
            with self.assertRaisesRegex(RuntimeError, "conversation_memory_unavailable"):
                await memory.append("privado", "privado", source="local_llm", epoch=0)
        self.cur.execute.assert_not_awaited()

    async def test_forget_requires_exact_confirmation_and_changes_generation(self) -> None:
        with self.assertRaisesRegex(ValueError, "confirmation_required"):
            await self.memory.clear("sí")
        self.cur.execute.assert_not_awaited()
        self.assertEqual(await self.memory.clear("BORRAR MEMORIA"), 3)
        params = self.cur.execute.call_args.args[1]
        self.assertEqual(self.cipher.decrypt(params[0], "setting:conversation_epoch"), 1)

    async def test_search_terms_are_bound_hmacs_not_sql_or_plaintext(self) -> None:
        self.cur.fetchall.return_value = []
        await self.memory.page(query="fusible'; DROP TABLE notas; --", before=20)
        sql, params = self.cur.execute.call_args.args
        self.assertNotIn("fusible", sql)
        self.assertNotIn("DROP", sql)
        self.assertEqual(params[0], 20)
        self.assertTrue(any(isinstance(p, str) and len(p) == 64 for p in params))
        self.assertEqual(required_scope('/api/v1/conversation/history/', 'POST'), 'memory:read')
        self.assertEqual(required_scope('/api/v1/conversation/forget/', 'POST'), 'memory:write')

    async def test_recall_finds_old_match_and_keeps_context_bounded(self) -> None:
        def row(number: int):
            unique = f"{number:032x}"
            return (number, unique, datetime(2026, 11, 2), "local_llm", self.cipher.encrypt(
                {"prompt": "fusible " + "x" * 1500, "reply": "F9 " + "y" * 3000}, f"conversation:{unique}"))
        self.memory.epoch = AsyncMock(return_value=0)
        self.cur.fetchall.side_effect = [[row(50), row(49), row(48)], [(2,)], [row(2)]]
        epoch, result = await self.memory.recall("recuerdas el fusible")
        self.assertEqual(epoch, 0)
        self.assertIn(2, [r["id"] for r in result])
        self.assertLessEqual(len(json.dumps(result, ensure_ascii=True).encode()), 2400)

    async def test_index_is_deterministic_across_restarts_but_key_specific(self) -> None:
        a = self.memory.term_hashes("Electrónica, FUSIBLES")
        self.assertEqual(a, ConversationMemory(self.audit).term_hashes("electronica fusible"))
        other = ConversationMemory(AuditStore(enabled=True, cipher=StorageCipher(os.urandom(32))))
        self.assertNotEqual(a, other.term_hashes("electronica fusible"))
        self.assertNotIn("que", words("¿recuerdas que hablamos de electrónica?"))


class LocalOcrDocumentTests(unittest.IsolatedAsyncioTestCase):
    async def test_ocr_text_is_local_owned_and_available_for_document_questions(self) -> None:
        documents = DocumentService()
        original = blank_pdf()
        uploaded = await documents.upload('operator-a', original, 'escaneado.pdf')
        with self.assertRaisesRegex(ValueError, 'document_not_found'):
            documents.add_ocr('operator-b', uploaded['id'], 1, 'El fusible es F9.', 90)
        result = documents.add_ocr('operator-a', uploaded['id'], 1, 'El fusible es F9.', 90)
        self.assertEqual(documents.get('operator-a', uploaded['id']).raw, original)
        self.assertTrue(result['ocr'])
        self.assertIn('F9', question_excerpts(result['pages'], 'qué fusible')[0]['text'])
        for page, text, confidence in [(True, 'texto', 80), (2, 'texto', 80), (1, 'x'*3001, 80), (1, 'texto', float('nan'))]:
            with self.assertRaisesRegex(ValueError, 'document_invalid_ocr'):
                documents.add_ocr('operator-a', uploaded['id'], page, text, confidence)
