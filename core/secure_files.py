"""Archivos privados atomicos. Todas sus funciones se ejecutan en workers."""
from __future__ import annotations

import csv
import ctypes
import hashlib
import io
import os
import re
import subprocess
import tempfile
from pathlib import Path

from thorn_web.credentials import dpapi

MAGIC = b'THORN-SEALED-1\x00'


def private_permissions(path: Path, *, directory: bool = False) -> None:
    """ACL del usuario actual y SYSTEM; sin depender del idioma de Windows."""
    os.chmod(path, 0o700 if directory else 0o600)
    if os.name != 'nt':
        return
    system = Path(os.environ['SYSTEMROOT']) / 'System32'
    identity = subprocess.run([str(system / 'whoami.exe'), '/user', '/fo', 'csv', '/nh'],
        capture_output=True, check=True, creationflags=subprocess.CREATE_NO_WINDOW)
    sid = next(csv.reader(io.StringIO(identity.stdout.decode(errors='replace'))))[1]
    if not re.fullmatch(r'S-1-[0-9-]+', sid):
        raise OSError('private_acl_unavailable')
    permission = '(OI)(CI)(F)' if directory else '(F)'
    subprocess.run([str(system / 'icacls.exe'), str(path), '/inheritance:r', '/grant:r',
        f'*{sid}:{permission}', f'*S-1-5-18:{permission}'], check=True,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)


def safe_path(path: Path, root: Path) -> None:
    boundary = root.resolve()
    if path.is_symlink() or not path.resolve().is_relative_to(boundary) or path.resolve() == boundary:
        raise OSError('invalid_private_path')


def atomic_private_write(path: Path, data: bytes, *, root: Path, replace: bool = False) -> None:
    """Crea sin sobrescribir o reemplaza atomicamente un archivo ya autenticado."""
    safe_path(path, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    safe_path(path.parent, root)
    private_permissions(path.parent, directory=True)
    temporary: Path | None = None
    try:
        # NOTA DEL OPERADOR: se escribe primero al lado del destino y se fuerza
        # a disco. Luego se publica el archivo completo mediante rename/link.
        # Asi evitamos dejar medio archivo como version activa si hay un corte.
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.thorn-', delete=False) as stream:
            temporary = Path(stream.name)
            private_permissions(temporary)
            stream.write(data); stream.flush(); os.fsync(stream.fileno())
        if replace:
            os.replace(temporary, path)
        else:
            # Un hardlink es atomico y falla si el destino ya existe (NTFS/POSIX).
            os.link(temporary, path)
        temporary.unlink(missing_ok=True); temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def read_private(path: Path, *, root: Path, maximum: int) -> bytes:
    safe_path(path, root)
    with path.open('rb') as stream:
        result = stream.read(maximum + 1)
    if len(result) > maximum:
        raise OSError('private_file_too_large')
    return result


def sealed_read(root: Path, name: str, *, maximum: int = 262144) -> bytes:
    """DPAPI autentica el contenido y lo vincula al usuario y la ruta de datos."""
    raw = read_private(root / 'data/security' / name, root=root, maximum=maximum)
    if not raw.startswith(MAGIC):
        raise OSError('invalid_sealed_file')
    # La ruta forma parte de la proteccion: copiar este blob a otra carpeta o
    # usuario no equivale a restaurarlo. Para eso existe el respaldo recuperable.
    entropy = hashlib.sha256((str(root.resolve()) + '|THORN-B8|' + name).encode()).digest()
    return dpapi(raw[len(MAGIC):], entropy, decrypt=True)


def sealed_write(root: Path, name: str, data: bytes, *, replace: bool = False) -> None:
    if not re.fullmatch(r'[a-z0-9_-]+\.dpapi', name):
        raise ValueError('invalid_sealed_name')
    entropy = hashlib.sha256((str(root.resolve()) + '|THORN-B8|' + name).encode()).digest()
    atomic_private_write(root / 'data/security' / name, MAGIC + dpapi(data, entropy), root=root, replace=replace)
