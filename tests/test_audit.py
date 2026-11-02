"""SQL crudo parametrizado y transacciones de memoria/auditoria."""

from __future__ import annotations

import json
import unittest
from contextlib import asynccontextmanager
from datetime import datetime
from unittest.mock import AsyncMock, Mock, patch

from core.audit import AuditStore


class AuditTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.conn = Mock(commit=AsyncMock())
        self.cur = Mock(execute=AsyncMock(), fetchone=AsyncMock(), fetchall=AsyncMock(), lastrowid=42)
        @asynccontextmanager
        async def session():
            yield self.conn, self.cur
        self.patch = patch("core.audit.db_session", session)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.audit = AuditStore(enabled=True)

    async def test_initialize_does_not_recreate_existing_tables(self) -> None:
        self.cur.fetchall.return_value = [("thorn_audit",), ("thorn_settings",)]
        await self.audit.initialize()
        self.assertEqual(self.cur.execute.await_count, 1)
        self.conn.commit.assert_awaited_once()

    async def test_append_uses_bound_values_and_explicit_commit(self) -> None:
        value = "'; DROP TABLE thorn_audit; --"
        result = await self.audit.append("test", "observed", value, {"id": 1})
        sql, params = self.cur.execute.call_args.args
        self.assertNotIn(value, sql)
        self.assertIn(value, params)
        self.assertEqual(result, 42)
        self.conn.commit.assert_awaited_once()

    async def test_history_keyset_pagination_and_utc_dates(self) -> None:
        self.cur.fetchall.return_value = [(1, datetime(2026, 10, 6), "test", "observed", "Test", '{}')]
        rows = await self.audit.history(before=30, limit=10)
        sql, params = self.cur.execute.call_args.args
        self.assertIn("WHERE id < %s", sql)
        self.assertEqual(params, (30, 10))
        self.assertTrue(rows[0]["created_at"].endswith("+00:00"))
        for kwargs in ({"limit": 101}, {"before": 0}, {"limit": 0}):
            with self.assertRaises(ValueError):
                await self.audit.history(**kwargs)

    async def test_memory_and_metadata_commit_atomically(self) -> None:
        self.cur.fetchone.return_value = ("[]",)
        await self.audit.update_memory(text="Mi preferencia privada")
        calls = self.cur.execute.call_args_list
        self.assertIn("FOR UPDATE", calls[1].args[0])
        notes = json.loads(calls[2].args[1][0])
        self.assertEqual(notes[0]["text"], "Mi preferencia privada")
        self.assertNotIn("Mi preferencia privada", str(calls[3].args[1]))
        self.conn.commit.assert_awaited_once()

    async def test_memory_capacity_and_invalid_notes_do_not_commit(self) -> None:
        self.cur.fetchone.return_value = (json.dumps([{"id": str(i)} for i in range(20)]),)
        with self.assertRaisesRegex(ValueError, "memory_capacity_reached"):
            await self.audit.update_memory(text="new")
        for text in ("", "x" * 1001):
            with self.assertRaises(ValueError):
                await self.audit.update_memory(text=text)
        self.conn.commit.assert_not_awaited()

    async def test_delete_only_exact_note_and_records_metadata(self) -> None:
        notes = [{"id": "a" * 32, "text": "private", "created_at": "2026-10-06"},
                 {"id": "b" * 32, "text": "keep", "created_at": "2026-10-06"}]
        self.cur.fetchone.return_value = (json.dumps(notes),)
        await self.audit.update_memory(delete_id="a" * 32)
        self.assertEqual(json.loads(self.cur.execute.call_args_list[2].args[1][0]), notes[1:])
        self.assertIn("deleted", self.cur.execute.call_args_list[3].args[1])
        self.conn.commit.assert_awaited_once()

    async def test_unicode_storage_budget_and_invalid_surrogates_are_rejected(self) -> None:
        for text in ("\ud800", ""):
            with self.assertRaises(ValueError):
                await self.audit.update_memory(text=text)
        notes = [{"id": str(i), "text": "\U0001F642" * 1000, "created_at": "2026-10-06"} for i in range(15)]
        self.cur.fetchone.return_value = (json.dumps(notes),)
        with self.assertRaisesRegex(ValueError, "memory_storage_full"):
            await self.audit.update_memory(text="\U0001F642" * 1000)
        self.conn.commit.assert_not_awaited()
