"""Descarga el modelo base del modelo de interacción (Qwen2.5-0.5B-Instruct, Apache-2.0).

Revisión fija del repositorio de Hugging Face y verificación de cada archivo:
SHA-256 para los pesos (LFS) y hash de blob de Git para los archivos pequeños.
No se ejecuta al iniciar Thorn; solo prepara el entrenamiento de la Fase 2.

    python tools/install_interaction_base.py
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import sys

import httpx

ROOT = Path(__file__).resolve().parents[1]
REPO = "Qwen/Qwen2.5-0.5B-Instruct"
REVISION = "7ae557604adf67be50417f59c2c2f167def9a775"
TARGET = ROOT / ".models" / "qwen2.5-0.5b-instruct"
# archivo -> ("sha256" | "git", hash esperado, tamaño)
FILES = {
    "config.json": ("git", "0dbb161213629a23f0fc00ef286e6b1e366d180f", 659),
    "generation_config.json": ("git", "dfc11073787daf1b0f9c0f1499487ab5f4c93738", 242),
    "merges.txt": ("git", "20024bfe7c83998e9aeaf98a0cd6a2ce6306c2f0", 1671839),
    "tokenizer.json": ("git", "443909a61d429dff23010e5bddd28ff530edda00", 7031645),
    "tokenizer_config.json": ("git", "07bfe0640cb5a0037f9322287fbfc682806cf672", 7305),
    "vocab.json": ("git", "4783fe10ac3adce15ac8f358ef5462739852c569", 2776833),
    "LICENSE": ("git", "6634c8cc3133b3848ec74b9f275acaaa1ea618ab", 11343),
    "model.safetensors": ("sha256", "fdf756fa7fcbe7404d5c60e26bff1a0c8b8aa1f72ced49e7dd0210fe288fb7fe", 988097824),
}


def digest(kind: str, size: int):
    if kind == "git":
        hasher = hashlib.sha1()
        hasher.update(f"blob {size}\0".encode())
        return hasher
    return hashlib.sha256()


def download(client: httpx.Client, name: str, kind: str, expected: str, size: int) -> None:
    target = TARGET / name
    if target.is_file() and target.stat().st_size == size:
        hasher = digest(kind, size)
        with target.open("rb") as stream:
            for block in iter(lambda: stream.read(1 << 20), b""):
                hasher.update(block)
        if hasher.hexdigest() == expected:
            print(f"ya verificado  {name}")
            return
    pending = target.with_name(target.name + ".pendiente")
    hasher, received = digest(kind, size), 0
    url = f"https://huggingface.co/{REPO}/resolve/{REVISION}/{name}"
    with client.stream("GET", url) as response, pending.open("wb") as stream:
        response.raise_for_status()
        for block in response.iter_bytes(1 << 20):
            received += len(block)
            if received > size:
                raise ValueError(f"{name}: más grande de lo esperado")
            hasher.update(block)
            stream.write(block)
        stream.flush()
        os.fsync(stream.fileno())
    if received != size or hasher.hexdigest() != expected:
        pending.unlink(missing_ok=True)
        raise ValueError(f"{name}: la verificación falló; no se instala")
    os.replace(pending, target)
    print(f"descargado     {name} ({size / 1e6:.1f} MB)")


def main() -> int:
    TARGET.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=httpx.Timeout(60, connect=10), follow_redirects=True, trust_env=False) as client:
        for name, (kind, expected, size) in FILES.items():
            download(client, name, kind, expected, size)
    (TARGET / "ORIGEN.txt").write_text(f"{REPO}@{REVISION}\nLicencia: Apache-2.0\n", encoding="utf-8")
    print(f"Modelo base listo en {TARGET}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
