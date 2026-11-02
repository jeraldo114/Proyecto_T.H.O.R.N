"""Limites de migracion/recuperacion: no tocar bases existentes ni perder claves."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, Mock, patch

from core.secure_storage import StorageCipher, StorageUnavailable
from tools.migrate_secure_storage import decode_record, require_offline, restore_database


class MigrationSafetyTests(unittest.TestCase):
    def test_resume_can_read_old_and_new_key_without_plaintext_runtime_fallback(self) -> None:
        old, new = StorageCipher(os.urandom(32)), StorageCipher(os.urandom(32))
        value = {'text': 'sensitive-fixture'}
        for cipher in (old, new):
            encoded = cipher.encrypt(value, 'setting:memory')
            self.assertEqual(decode_record(encoded, 'setting:memory', (old, new)), value)
            with self.assertRaises(StorageUnavailable): decode_record(encoded, 'setting:alerts', (old, new))
        self.assertEqual(decode_record('[]', 'setting:memory', old), [])
        with self.assertRaises(StorageUnavailable): old.decrypt('[]', 'setting:memory')

    def test_pending_migration_blocks_boot_before_loading_secrets(self) -> None:
        from thorn_web.config import protected_environment
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); (root / 'data/security').mkdir(parents=True)
            (root / 'data/security/migration.pending').write_text('pending')
            with patch.dict(os.environ, {'THORN_SECURE_STORAGE': '0'}), patch('thorn_web.config.load_vault') as loader:
                with self.assertRaises(StorageUnavailable): protected_environment(root, set())
                loader.assert_not_called()

    def test_live_supported_runtime_prevents_migration_but_compiler_does_not(self) -> None:
        root = Path.cwd().resolve()
        app = Mock(info={'pid': os.getpid()+1, 'name': 'python.exe', 'cmdline': ['python', 'serve.py'], 'cwd': str(root)})
        with patch('psutil.process_iter', return_value=[app]):
            with self.assertRaisesRegex(RuntimeError, 'runtime_must_be_stopped'): require_offline(root)
        compiler = Mock(info={**app.info, 'cmdline': ['python', '-m', 'nuitka', str(root / 'desktop.py')]})
        with patch('psutil.process_iter', return_value=[compiler]): require_offline(root)


class RestoreDatabaseSafetyTests(unittest.IsolatedAsyncioTestCase):
    async def test_existing_database_is_never_dropped(self) -> None:
        from core.secure_storage import Vault, canonical
        import json
        payload = {'vault': json.loads(Vault(StorageCipher(os.urandom(32)), {}).serialize())}
        cur = Mock(execute=AsyncMock(side_effect=RuntimeError('database_exists')))
        conn = Mock()
        @asynccontextmanager
        async def session(): yield conn, cur
        with patch('core.db_core.db_session', session):
            with self.assertRaisesRegex(RuntimeError, 'database_exists'):
                await restore_database(payload, 'db_thorn_restore_' + 'a' * 16, temporary=True)
        self.assertEqual(cur.execute.await_count, 1)
        self.assertTrue(cur.execute.call_args.args[0].startswith('CREATE DATABASE'))

    async def test_live_or_arbitrary_database_name_is_rejected_before_sql(self) -> None:
        with patch('core.db_core.db_session') as session:
            for name in ('db_thorn', 'mysql', 'db_thorn_restore_abc;DROP DATABASE db_thorn'):
                with self.assertRaisesRegex(ValueError, 'invalid_restore_database'):
                    await restore_database({}, name, temporary=True)
            session.assert_not_called()
