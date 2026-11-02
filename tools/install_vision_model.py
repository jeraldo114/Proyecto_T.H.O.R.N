"""Instala el detector publico de manos; no abre la camara ni descarga al iniciar."""
from __future__ import annotations

import asyncio
import hashlib
import os
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.paths import data_root

URL = 'https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/latest/hand_landmarker.task'
SHA256 = 'fbc2a30080c3c557093b5ddfc334698132eb341044ccee322ccf8bcf3607cde1'


def install_bytes(raw: bytes) -> None:
    if hashlib.sha256(raw).hexdigest() != SHA256:
        raise ValueError('vision_model_checksum_failed')
    root = data_root().resolve()
    target = root / '.models/hand_landmarker.task'
    if target.is_symlink() or not target.resolve().is_relative_to(root):
        raise ValueError('invalid_model_path')
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix('.task.pending')
    if temporary.exists():
        raise ValueError('vision_install_pending')
    try:
        with temporary.open('xb') as stream:
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


async def main() -> None:
    async with httpx.AsyncClient(timeout=40, follow_redirects=False, trust_env=False) as client:
        async with client.stream('GET', URL) as response:
            response.raise_for_status()
            data = bytearray()
            async for chunk in response.aiter_bytes():
                data.extend(chunk)
                if len(data) > 16 * 1024 * 1024:
                    raise ValueError('vision_model_too_large')
    await asyncio.to_thread(install_bytes, bytes(data))
    print('Detector de manos verificado. Compila el HUD para incluirlo; camara apagada.')


if __name__ == '__main__':
    asyncio.run(main())
