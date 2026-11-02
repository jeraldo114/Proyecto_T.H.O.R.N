"""Firma Ed25519 del manifiesto de distribucion; no equivale a Authenticode."""
from __future__ import annotations

import argparse
import hashlib
import json
import mmap
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from core.secure_files import atomic_private_write, sealed_read, sealed_write
from core.secure_storage import canonical, encode64, decode64
from core.version import RELEASE

DOMAIN = b'THORN-RELEASE-v1\x00'
GENERATED = frozenset({'release-manifest.json', 'release-manifest.sig', 'release-verification.pub'})


def file_digest(path: Path, secrets: tuple[bytes, ...] = ()) -> str:
    with path.open('rb') as stream:
        if path.stat().st_size:
            with mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ) as data:
                if any(data.find(secret) >= 0 for secret in secrets): raise ValueError('release_contains_private_data')
        stream.seek(0)
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def release_files(directory: Path) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for path in directory.rglob('*'):
        if path.is_symlink() or getattr(path, 'is_junction', lambda: False)() or not path.resolve().is_relative_to(directory.resolve()):
            raise ValueError('release_contains_link')
        if not path.is_file(): continue
        relative = path.relative_to(directory).as_posix()
        if relative in GENERATED: continue
        if (any(part in {'data', 'backups', '.models', '.venv', '__pycache__', '.git'} for part in path.relative_to(directory).parts)
            or path.suffix.lower() in {'.py', '.pyc', '.env', '.key', '.dpapi', '.thornbak', '.pfx'}
            or path.name.startswith('.env') and path.name != '.env.example'):
            raise ValueError('release_contains_private_or_source_file')
        result[relative] = path
    if len(result) > 10000 or not result: raise ValueError('invalid_release_size')
    return result


def sign(directory: Path, key: Ed25519PrivateKey, private_values: tuple[bytes, ...]) -> dict[str, object]:
    files = release_files(directory)
    public = key.public_key().public_bytes_raw()
    value = {'v': 1, 'product': 'T.H.O.R.N', 'version': RELEASE, 'algorithm': 'Ed25519',
        'created_at': datetime.now(timezone.utc).isoformat(), 'signer': hashlib.sha256(public).hexdigest(),
        'files': {name: file_digest(path, private_values) for name,path in sorted(files.items())}}
    encoded = canonical(value)
    # Estos tres archivos contienen solo metadatos y una clave publica.
    (directory / 'release-manifest.json').write_bytes(encoded)
    (directory / 'release-manifest.sig').write_bytes(key.sign(DOMAIN + encoded))
    (directory / 'release-verification.pub').write_bytes(public)
    verify(directory, public)
    return value


def verify(directory: Path, trusted_public: bytes) -> dict[str, object]:
    encoded = (directory / 'release-manifest.json').read_bytes()
    if len(encoded) > 4 * 1024 * 1024: raise ValueError('invalid_release_manifest')
    Ed25519PublicKey.from_public_bytes(trusted_public).verify((directory / 'release-manifest.sig').read_bytes(), DOMAIN + encoded)
    value = json.loads(encoded)
    if value.get('v') != 1 or value.get('algorithm') != 'Ed25519' or value.get('signer') != hashlib.sha256(trusted_public).hexdigest():
        raise ValueError('invalid_release_manifest')
    files = release_files(directory)
    if set(value['files']) != set(files): raise ValueError('release_files_changed')
    for name, path in files.items():
        if file_digest(path) != value['files'][name]: raise ValueError('release_file_modified')
    return value


def initialize_signer(root: Path) -> Ed25519PrivateKey:
    target = root / 'data/security/release-signing.dpapi'
    public = root / 'data/security/release-signing.pub'
    if target.exists():
        key = Ed25519PrivateKey.from_private_bytes(sealed_read(root, 'release-signing.dpapi'))
        if public.exists() and public.read_bytes() != key.public_key().public_bytes_raw():
            raise ValueError('release_signer_conflict')
        if not public.exists(): atomic_private_write(public, key.public_key().public_bytes_raw(), root=root)
        return key
    if public.exists(): raise ValueError('release_signing_key_missing')
    key = Ed25519PrivateKey.generate()
    sealed_write(root, 'release-signing.dpapi', key.private_bytes_raw())
    atomic_private_write(public, key.public_key().public_bytes_raw(), root=root)
    return key


def load_trusted_public(path: Path) -> bytes:
    raw = path.read_bytes()
    if len(raw) == 32: return raw
    if len(raw) > 4096: raise ValueError('invalid_trusted_public_key')
    value = json.loads(raw)
    public = decode64(value['public_key'], 32)
    if value.get('algorithm') != 'Ed25519' or value.get('fingerprint') != hashlib.sha256(public).hexdigest():
        raise ValueError('invalid_trusted_public_key')
    return public


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--initialize', action='store_true')
    group.add_argument('--sign', type=Path)
    group.add_argument('--verify', type=Path)
    group.add_argument('--export-public', type=Path, help='Exportar solo la clave publica verificable para el repositorio.')
    parser.add_argument('--data-dir', type=Path, default=ROOT)
    parser.add_argument('--trusted-key', type=Path)
    args = parser.parse_args()
    try:
        if args.verify:
            trusted = args.trusted_key or args.data_dir / 'data/security/release-signing.pub'
            value = verify(args.verify.resolve(), load_trusted_public(trusted))
        else:
            key = initialize_signer(args.data_dir.resolve())
            if args.export_public:
                public = key.public_key().public_bytes_raw()
                exported = canonical({'algorithm': 'Ed25519', 'public_key': encode64(public),
                    'fingerprint': hashlib.sha256(public).hexdigest()}) + b'\n'
                destination = args.export_public.resolve()
                if destination.exists() and destination.read_bytes() != exported:
                    raise ValueError('existing_trusted_key_differs')
                if not destination.exists(): destination.write_bytes(exported)
                print('Clave PUBLICA exportada; no contiene material privado:', destination); return 0
            if args.initialize:
                print('Clave de firma protegida creada/verificada. Ninguna clave privada se muestra.'); return 0
            from tools.check_repository import local_secret_variants
            private_values = tuple(local_secret_variants()) + (key.private_bytes_raw(), encode64(key.private_bytes_raw()).encode(),)
            value = sign(args.sign.resolve(), key, private_values)
        print('Manifiesto Ed25519 verificado:', len(value['files']), 'archivos. Huella publica:', value['signer'])
        return 0
    except Exception as exc:
        print('Distribucion rechazada:', type(exc).__name__); return 1


if __name__ == '__main__':
    raise SystemExit(main())
