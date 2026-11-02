"""Cifrado, autenticacion, permisos de archivo y SQL sin contenido privado."""
from __future__ import annotations

import asyncio
import json
import os
import tempfile
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from cryptography.hazmat.primitives.asymmetric import ed25519, rsa

from core.audit import AuditStore
from core.secure_backup import RecoveryKeys, seal_backup, open_backup
from core.secure_storage import StorageCipher, StorageUnavailable, Vault, load_vault, save_vault


class CipherTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cipher = StorageCipher(os.urandom(32))

    def test_unicode_roundtrip_new_nonce_and_no_plaintext(self) -> None:
        value = [{'text': 'Mi nota privada: ñ, café, 日本語, 🙂', 'id': 'a' * 32}]
        first = self.cipher.encrypt(value, 'setting:memory')
        second = self.cipher.encrypt(value, 'setting:memory')
        self.assertNotIn('Mi nota privada', first); self.assertNotEqual(first, second)
        self.assertEqual(self.cipher.decrypt(first, 'setting:memory'), value)
        self.assertEqual(self.cipher.decrypt(second, 'setting:memory'), value)
        self.assertNotIn(self.cipher.key.hex(), repr(self.cipher))

    def test_wrong_key_context_tampering_and_plaintext_fail_closed(self) -> None:
        first = self.cipher.encrypt({'private': 'value'}, 'audit:42')
        value = json.loads(first); value['ciphertext'] = 'AAAA' + value['ciphertext'][4:]
        for cipher, payload, context in ((self.cipher, first, 'audit:43'),
            (StorageCipher(os.urandom(32)), first, 'audit:42'),
            (self.cipher, json.dumps(value), 'audit:42'), (self.cipher, '{}', 'audit:42'),
            (self.cipher, first[:-20], 'audit:42')):
            with self.assertRaisesRegex(StorageUnavailable, 'secure_storage_unavailable'):
                cipher.decrypt(payload, context)

    def test_ciphertext_budget_allows_large_valid_unicode_records(self) -> None:
        value = '🙂' * 14000
        sealed = self.cipher.encrypt(value, 'setting:memory')
        self.assertGreater(len(sealed), 65535)
        self.assertEqual(self.cipher.decrypt(sealed, 'setting:memory'), value)
        with self.assertRaisesRegex(ValueError, 'secure_record_too_large'):
            self.cipher.encrypt('a' * 60001, 'setting:memory')

    def test_missing_vault_is_not_recreated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(StorageUnavailable): load_vault(root)
            self.assertFalse((root / 'data').exists())

    @unittest.skipUnless(os.name == 'nt', 'DPAPI solo Windows')
    def test_vault_roundtrip_protects_secret_and_detects_damage(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); vault = Vault(self.cipher, {'MYSQL_PASSWORD': 'test-only-secret-value'})
            save_vault(vault, root)
            target = root / 'data/security/vault.dpapi'
            self.assertNotIn(b'test-only-secret-value', target.read_bytes())
            self.assertEqual(load_vault(root).serialize(), vault.serialize())
            with self.assertRaises(FileExistsError): save_vault(vault, root)
            target.write_bytes(b'corrupt')
            with self.assertRaises(StorageUnavailable): load_vault(root)

    def test_process_overrides_work_without_plaintext_fallback(self) -> None:
        from thorn_web.config import protected_environment
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); (root / 'data/security').mkdir(parents=True)
            (root / 'data/security/vault.dpapi').write_bytes(b'fixture')
            with patch.dict(os.environ, {'THORN_SECURE_STORAGE': '0', 'MYSQL_PASSWORD': 'process-only'}, clear=True), \
                 patch('thorn_web.config.load_vault', return_value=Vault(self.cipher,
                    {'MYSQL_PASSWORD': 'vault-only', 'DJANGO_SECRET_KEY': 'protected-test-key'})):
                self.assertTrue(protected_environment(root, {'MYSQL_PASSWORD'}))
                self.assertEqual(os.environ['MYSQL_PASSWORD'], 'process-only')
                self.assertEqual(os.environ['DJANGO_SECRET_KEY'], 'protected-test-key')
                self.assertEqual(os.environ['THORN_SECURE_STORAGE'], '1')


class BackupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.keys = RecoveryKeys(rsa.generate_private_key(public_exponent=65537, key_size=3072), ed25519.Ed25519PrivateKey.generate())

    def test_portable_recovery_and_no_plaintext(self) -> None:
        original = b'private-database-and-keys'
        sealed = seal_backup(original, self.keys)
        recovered = RecoveryKeys.parse(self.keys.serialize())
        self.assertNotIn(original, sealed)
        self.assertEqual(open_backup(sealed, recovered), original)
        self.assertNotEqual(seal_backup(original, self.keys), sealed)

    def test_signature_pinning_tampering_and_truncation(self) -> None:
        sealed = seal_backup(b'private', self.keys)
        value = json.loads(sealed); value['ciphertext'] = 'AAAA' + value['ciphertext'][4:]
        wrong_signer = RecoveryKeys(self.keys.recipient, ed25519.Ed25519PrivateKey.generate())
        for body, keys in ((json.dumps(value).encode(), self.keys), (sealed[:-10], self.keys), (sealed, wrong_signer)):
            with self.assertRaisesRegex(ValueError, 'backup_authentication_failed'):
                open_backup(body, keys)


class ProtectedAuditTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.cipher = StorageCipher(os.urandom(32))
        self.conn = Mock(commit=AsyncMock())
        self.cur = Mock(execute=AsyncMock(), fetchone=AsyncMock(), fetchall=AsyncMock(), lastrowid=42)
        @asynccontextmanager
        async def session(): yield self.conn, self.cur
        self.patcher = patch('core.audit.db_session', session); self.patcher.start(); self.addCleanup(self.patcher.stop)
        self.audit = AuditStore(enabled=True, cipher=self.cipher)

    async def test_note_and_audit_commit_without_plaintext_sql(self) -> None:
        self.cur.fetchone.return_value = (self.cipher.encrypt([], 'setting:memory'),)
        identity = await self.audit.update_memory(text='private-note-text')
        calls = self.cur.execute.call_args_list
        self.assertNotIn('private-note-text', repr(calls))
        notes = self.cipher.decrypt(calls[2].args[1][0], 'setting:memory')
        self.assertEqual(notes[0]['text'], 'private-note-text')
        details = self.cipher.decrypt(calls[4].args[1][0], 'audit:42')
        self.assertEqual(details, {'note_id': identity}); self.conn.commit.assert_awaited_once()

    async def test_audit_details_bound_to_database_identity(self) -> None:
        await self.audit.append('test', 'observed', 'Metadata', {'private': 'secret-detail'})
        self.assertNotIn('secret-detail', repr(self.cur.execute.call_args_list))
        encoded = self.cur.execute.call_args_list[1].args[1][0]
        self.assertEqual(self.cipher.decrypt(encoded, 'audit:42'), {'private': 'secret-detail'})
        with self.assertRaises(StorageUnavailable): self.cipher.decrypt(encoded, 'audit:43')

    async def test_secure_mode_without_initialization_never_writes_plaintext(self) -> None:
        with patch.dict(os.environ, {'THORN_SECURE_STORAGE': '1'}):
            audit = AuditStore(enabled=True)
        with self.assertRaises(StorageUnavailable): await audit.save_setting('memory', [])
        self.cur.execute.assert_not_awaited()
