"""Respaldos autenticados y portables; no dependen de la identidad DPAPI original."""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from cryptography.exceptions import InvalidSignature, InvalidTag
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .secure_files import atomic_private_write, read_private, private_permissions
from .secure_storage import canonical, encode64, decode64
from thorn_web.credentials import file_lock

MAX_BACKUP = 32 * 1024 * 1024
MAX_FILE = 46 * 1024 * 1024
BACKUP_DOMAIN = b'THORN-B8-BACKUP-v1\x00'


@dataclass(frozen=True, slots=True)
class RecoveryKeys:
    """Dos claves distintas para confidencialidad y firma del respaldo."""
    recipient: rsa.RSAPrivateKey = field(repr=False)
    signer: ed25519.Ed25519PrivateKey = field(repr=False)

    @property
    def recipient_id(self) -> str:
        return hashlib.sha256(self.recipient.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)).hexdigest()

    @property
    def signing_public(self) -> bytes:
        return self.signer.public_key().public_bytes_raw()

    def serialize(self) -> bytes:
        return canonical({'v': 1, 'recipient': self.recipient.private_bytes(serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode('ascii'),
            'signer': encode64(self.signer.private_bytes_raw())})

    @classmethod
    def parse(cls, raw: bytes) -> RecoveryKeys:
        try:
            value = json.loads(raw)
            if not isinstance(value, dict) or set(value) != {'v', 'recipient', 'signer'} or value['v'] != 1:
                raise ValueError('invalid_recovery_key')
            recipient = serialization.load_pem_private_key(value['recipient'].encode('ascii'), password=None)
            if not isinstance(recipient, rsa.RSAPrivateKey) or recipient.key_size < 3072:
                raise ValueError('invalid_recovery_key')
            return cls(recipient, ed25519.Ed25519PrivateKey.from_private_bytes(decode64(value['signer'], 32)))
        except (ValueError, TypeError, KeyError, UnicodeError):
            raise ValueError('recovery_key_unavailable') from None

    @classmethod
    def load(cls, path: Path) -> RecoveryKeys:
        return cls.parse(read_private(path, root=path.parent, maximum=16384))


def initialize_recovery(root: Path) -> Path:
    """Solo el comando explicito inicializa. Nunca reemplaza una clave perdida."""
    directory = root / 'data/recovery'
    directory.mkdir(parents=True, exist_ok=True); private_permissions(directory, directory=True)
    target, public = directory / 'thorn-recovery.key', directory / 'thorn-recovery.pub'
    with file_lock(directory / 'recovery.lock'):
        if target.exists():
            RecoveryKeys.load(target); return target
        if public.exists():
            raise ValueError('recovery_key_missing')
        keys = RecoveryKeys(rsa.generate_private_key(public_exponent=65537, key_size=3072), ed25519.Ed25519PrivateKey.generate())
        atomic_private_write(target, keys.serialize(), root=root)
        atomic_private_write(public, canonical({'v': 1, 'recipient_id': keys.recipient_id,
            'recipient': keys.recipient.public_key().public_bytes(serialization.Encoding.PEM,
                serialization.PublicFormat.SubjectPublicKeyInfo).decode('ascii'),
            'signer': encode64(keys.signing_public)}), root=root)
        return target


def seal_backup(payload: bytes, keys: RecoveryKeys) -> bytes:
    if len(payload) > MAX_BACKUP:
        raise ValueError('backup_too_large')
    secret, nonce = AESGCM.generate_key(bit_length=256), os.urandom(12)
    wrapped = keys.recipient.public_key().encrypt(secret, padding.OAEP(
        mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=BACKUP_DOMAIN))
    header = {'v': 1, 'alg': 'RSA-OAEP-SHA256+A256GCM+Ed25519', 'recipient': keys.recipient_id,
        'signer': encode64(keys.signing_public), 'wrapped_key': encode64(wrapped), 'nonce': encode64(nonce)}
    body = {'header': header, 'ciphertext': encode64(AESGCM(secret).encrypt(nonce, payload, canonical(header)))}
    return canonical({**body, 'signature': encode64(keys.signer.sign(BACKUP_DOMAIN + canonical(body)))})


def open_backup(encoded: bytes, keys: RecoveryKeys) -> bytes:
    """Verifica con clave publica FIJADA por el archivo privado, no por el respaldo."""
    try:
        if len(encoded) > MAX_FILE: raise ValueError('backup_too_large')
        value = json.loads(encoded)
        if not isinstance(value, dict) or set(value) != {'header', 'ciphertext', 'signature'}:
            raise ValueError('invalid_backup')
        header = value['header']
        if (not isinstance(header, dict) or set(header) != {'v', 'alg', 'recipient', 'signer', 'wrapped_key', 'nonce'}
            or header['v'] != 1 or header['alg'] != 'RSA-OAEP-SHA256+A256GCM+Ed25519'
            or header['recipient'] != keys.recipient_id or header['signer'] != encode64(keys.signing_public)):
            raise ValueError('invalid_backup')
        body = {'header': header, 'ciphertext': value['ciphertext']}
        keys.signer.public_key().verify(decode64(value['signature'], 64), BACKUP_DOMAIN + canonical(body))
        secret = keys.recipient.decrypt(decode64(header['wrapped_key'], 512), padding.OAEP(
            mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=BACKUP_DOMAIN))
        nonce = decode64(header['nonce'], 12)
        if len(secret) != 32 or len(nonce) != 12: raise ValueError('invalid_backup')
        result = AESGCM(secret).decrypt(nonce, decode64(value['ciphertext'], MAX_BACKUP + 16), canonical(header))
        if len(result) > MAX_BACKUP: raise ValueError('backup_too_large')
        return result
    except (ValueError, TypeError, KeyError, InvalidSignature, InvalidTag, UnicodeError, RecursionError):
        raise ValueError('backup_authentication_failed') from None
