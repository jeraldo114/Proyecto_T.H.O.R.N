from pathlib import Path
import tempfile
import unittest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from tools.sign_release import sign, verify
from tools.package_release import assert_verified
from core.version import RELEASE
import hashlib
import json


class ReleaseTests(unittest.TestCase):
    def test_package_requires_the_same_binary_and_release_that_passed_verification(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); binary = root / 'THORN.exe'; binary.write_bytes(b'fixture')
            record = root / 'verification.json'
            value = {'compiled': True, 'encrypted_storage': True, 'pdf_text': True,
                     'context_follow_up': True, 'release': RELEASE,
                     'binary_sha256': hashlib.sha256(binary.read_bytes()).hexdigest()}
            record.write_text(json.dumps(value), encoding='utf-8')
            assert_verified(root, record)
            binary.write_bytes(b'new build')
            with self.assertRaisesRegex(ValueError, 'verify_current_compilation_first'):
                assert_verified(root, record)
            binary.write_bytes(b'fixture'); value['release'] = 'Bastion 8'
            record.write_text(json.dumps(value), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'verify_current_compilation_first'):
                assert_verified(root, record)

    def test_tampering_extra_files_and_wrong_signer_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); binary = root / 'THORN.exe'; binary.write_bytes(b'compiled-program-fixture')
            key = Ed25519PrivateKey.generate(); public = key.public_key().public_bytes_raw()
            sign(root, key, ())
            self.assertEqual(len(verify(root, public)['files']), 1)
            with self.assertRaises(InvalidSignature): verify(root, Ed25519PrivateKey.generate().public_key().public_bytes_raw())
            binary.write_bytes(b'altered-program')
            with self.assertRaisesRegex(ValueError, 'release_file_modified'): verify(root, public)
            binary.write_bytes(b'compiled-program-fixture'); (root / 'extra.dll').write_bytes(b'new')
            with self.assertRaisesRegex(ValueError, 'release_files_changed'): verify(root, public)

    def test_sources_plaintext_keys_and_actual_private_values_block_signing(self) -> None:
        for filename, content, private in [('config.env', b'public', ()), ('main.py', b'code', ()),
            ('authority.dpapi', b'protected', ()), ('THORN.exe', b'contains-test-secret', (b'test-secret',))]:
            with self.subTest(filename=filename), tempfile.TemporaryDirectory() as folder:
                root = Path(folder); (root / filename).write_bytes(content)
                with self.assertRaises(ValueError): sign(root, Ed25519PrivateKey.generate(), private)
