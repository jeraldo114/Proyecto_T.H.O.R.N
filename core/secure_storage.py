"""AES-256-GCM versionado: una clave de datos distinta de la autoridad del HUD."""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .paths import data_root
from .secure_files import sealed_read, sealed_write

SECRET_NAMES = frozenset({'MYSQL_PASSWORD', 'DJANGO_SECRET_KEY', 'ELEVENLABS_API_KEY',
                         'BRAVE_SEARCH_API_KEY'})
MAX_PLAINTEXT = 60000


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False).encode('ascii')


def encode64(value: bytes) -> str:
    return base64.b64encode(value).decode('ascii')


def decode64(value: object, maximum: int) -> bytes:
    if not isinstance(value, str) or len(value) > (maximum + 2) // 3 * 4:
        raise ValueError('invalid_ciphertext')
    result = base64.b64decode(value, validate=True)
    if len(result) > maximum:
        raise ValueError('invalid_ciphertext')
    return result


class StorageUnavailable(RuntimeError):
    """Error publico acotado: nunca incluye notas, claves ni texto descifrado."""
    def __init__(self) -> None:
        super().__init__('secure_storage_unavailable')


@dataclass(frozen=True, slots=True)
class StorageCipher:
    key: bytes = field(repr=False)

    def __post_init__(self) -> None:
        if len(self.key) != 32:
            raise ValueError('invalid_data_key')

    @property
    def identity(self) -> str:
        return hashlib.sha256(self.key).hexdigest()[:32]

    def encrypt(self, value: object, context: str) -> str:
        """Nonce aleatorio de 96 bits; el contexto impide mover registros."""
        plaintext = json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode('utf-8')
        if len(plaintext) > MAX_PLAINTEXT:
            raise ValueError('secure_record_too_large')
        header = {'v': 1, 'alg': 'A256GCM', 'kid': self.identity}
        # NOTA DEL OPERADOR: cada cifrado lleva un nonce nuevo. El contexto
        # autentica a que registro pertenece, aunque ambos usen la misma clave.
        nonce = os.urandom(12)
        aad = canonical({'header': header, 'context': context})
        return canonical({**header, 'nonce': encode64(nonce),
            'ciphertext': encode64(AESGCM(self.key).encrypt(nonce, plaintext, aad))}).decode('ascii')

    def decrypt(self, encoded: str, context: str) -> object:
        """No hay fallback a texto plano ni generacion automatica de otra clave."""
        try:
            if not isinstance(encoded, str) or len(encoded) > 82000:
                raise ValueError('invalid_ciphertext')
            value = json.loads(encoded)
            if (not isinstance(value, dict) or set(value) != {'v', 'alg', 'kid', 'nonce', 'ciphertext'} or
                value['v'] != 1 or value['alg'] != 'A256GCM' or value['kid'] != self.identity):
                raise ValueError('invalid_ciphertext')
            nonce = decode64(value['nonce'], 12)
            if len(nonce) != 12: raise ValueError('invalid_nonce')
            header = {key: value[key] for key in ('v', 'alg', 'kid')}
            raw = AESGCM(self.key).decrypt(nonce, decode64(value['ciphertext'], MAX_PLAINTEXT + 16),
                canonical({'header': header, 'context': context}))
            return json.loads(raw)
        except (ValueError, TypeError, KeyError, binascii.Error, InvalidTag, RecursionError, UnicodeError):
            # Si no autentica, frenamos. Inventar otra clave o aceptar texto
            # plano ocultaria el problema y no recuperaria el contenido original.
            raise StorageUnavailable() from None


@dataclass(frozen=True, slots=True)
class Vault:
    cipher: StorageCipher = field(repr=False)
    secrets: Mapping[str, str] = field(repr=False)

    def serialize(self) -> bytes:
        return canonical({'v': 1, 'data_key': encode64(self.cipher.key), 'kid': self.cipher.identity,
                          'secrets': dict(self.secrets)})

    @classmethod
    def parse(cls, raw: bytes) -> Vault:
        try:
            value = json.loads(raw)
            if not isinstance(value, dict) or set(value) != {'v', 'data_key', 'kid', 'secrets'} or value['v'] != 1:
                raise ValueError('invalid_vault')
            cipher = StorageCipher(decode64(value['data_key'], 32))
            entries = value['secrets']
            if (value['kid'] != cipher.identity or not isinstance(entries, dict) or not set(entries) <= SECRET_NAMES
                or any(not isinstance(v, str) or len(v) > 16384 for v in entries.values())):
                raise ValueError('invalid_vault')
            return cls(cipher, entries)
        except (ValueError, TypeError, KeyError, binascii.Error, UnicodeError):
            raise StorageUnavailable() from None


def vault_path(root: Path | None = None) -> Path:
    return (root or data_root()) / 'data/security/vault.dpapi'


def load_vault(root: Path | None = None) -> Vault:
    """Solo lectura; debe llamarse en un worker antes de aceptar HTTP."""
    try:
        return Vault.parse(sealed_read(root or data_root(), 'vault.dpapi'))
    except (OSError, ValueError):
        raise StorageUnavailable() from None


def save_vault(vault: Vault, root: Path, *, replace: bool = False) -> None:
    sealed_write(root, 'vault.dpapi', vault.serialize(), replace=replace)
    if load_vault(root).serialize() != vault.serialize():
        raise StorageUnavailable()
