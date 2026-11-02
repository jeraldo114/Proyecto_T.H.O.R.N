"""Autoridad local protegida por el usuario Windows. Nunca registra secretos."""

from __future__ import annotations

import ctypes
import hashlib
import os
import secrets
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


def dpapi(data: bytes, entropy: bytes, *, decrypt: bool = False) -> bytes:
    """DPAPI CurrentUser, no LOCAL_MACHINE; requiere la misma identidad Windows."""
    if os.name != "nt":
        raise OSError("windows_dpapi_required")
    from ctypes import wintypes
    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]
    def blob(value: bytes) -> tuple[Blob, object]:
        buffer = ctypes.create_string_buffer(value)
        return Blob(len(value), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte))), buffer
    source, source_buffer = blob(data)
    extra, extra_buffer = blob(entropy)
    result = Blob()
    library = ctypes.WinDLL("crypt32", use_last_error=True)
    function = library.CryptUnprotectData if decrypt else library.CryptProtectData
    function.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.POINTER(Blob),
                         ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    function.restype = wintypes.BOOL
    if not function(ctypes.byref(source), None, ctypes.byref(extra), None, None, 1, ctypes.byref(result)):
        raise OSError("credential_store_unavailable")
    try:
        return ctypes.string_at(result.data, result.size)
    finally:
        free = ctypes.WinDLL("kernel32").LocalFree
        free.argtypes = [ctypes.c_void_p]; free.restype = ctypes.c_void_p
        free(result.data)


@contextmanager
def file_lock(path: Path) -> Iterator[None]:
    """Serializa primeros arranques simultaneos. Ejecutar solamente en un worker."""
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    with os.fdopen(descriptor, "r+b") as stream:
        if stream.seek(0, 2) == 0:
            stream.write(b"0"); stream.flush()
        deadline = time.monotonic() + 5
        while True:
            try:
                stream.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise OSError("credential_store_busy") from None
                time.sleep(0.05)
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def root_credential(root: Path, existing: str | None = None) -> str:
    """Migra la autoridad existente sin rotarla ni sobrescribir un almacen distinto."""
    if existing and (not 32 <= len(existing) <= 256 or not existing.isascii()):
        raise OSError("invalid_root_credential")
    directory = root / "data" / "security"
    if not directory.resolve().is_relative_to(root.resolve()):
        raise OSError("invalid_credential_directory")
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    target = directory / "authority.dpapi"
    lock = directory / "authority.lock"
    if target.is_symlink() or lock.is_symlink():
        raise OSError("invalid_credential_store")
    entropy = hashlib.sha256((str(root.resolve()) + "|THORN-B8-authority-v1").encode()).digest()
    with file_lock(lock):
        if target.exists():
            if target.stat().st_size > 16384:
                raise OSError("invalid_credential_store")
            raw = target.read_bytes()
            if not raw.startswith(b"THORN-DPAPI-1\x00"):
                raise OSError("invalid_credential_store")
            value = dpapi(raw[14:], entropy, decrypt=True).decode("ascii")
            if existing and not secrets.compare_digest(existing, value):
                raise OSError("credential_store_conflict")
        else:
            value = existing or secrets.token_urlsafe(48)
            payload = b"THORN-DPAPI-1\x00" + dpapi(value.encode("ascii"), entropy)
            temporary: Path | None = None
            try:
                with tempfile.NamedTemporaryFile(dir=directory, prefix="authority-", delete=False) as output:
                    temporary = Path(output.name); os.chmod(temporary, 0o600)
                    output.write(payload); output.flush(); os.fsync(output.fileno())
                temporary.replace(target); temporary = None
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
        if not 32 <= len(value) <= 256 or not value.isascii():
            raise OSError("invalid_credential_store")
        return value
