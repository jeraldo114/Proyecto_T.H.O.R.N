"""Crea un perfil sobre pesos YA instalados; no entrena ni descarga modelos."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.local_llm import validate_endpoint
from core.personality import PERSONALITY
from core.brain_host import LocalBrainHost
from core.version import MODEL_PROFILE


async def create_profile() -> None:
    async with httpx.AsyncClient(base_url=validate_endpoint("http://127.0.0.1:11434"), trust_env=False, follow_redirects=False, timeout=120) as client:
        response = await client.get("/api/tags"); response.raise_for_status()
        candidates = [item for item in response.json().get("models", []) if item.get("name") == "qwen2.5:1.5b"]
        if not any(not item.get("remote_host") and not item.get("remote_model") for item in candidates):
            raise SystemExit("Falta qwen2.5:1.5b local. No se descarga ni se usa un proveedor remoto automaticamente.")
        response = await client.post("/api/create", json={"model": MODEL_PROFILE, "from": "qwen2.5:1.5b",
            "system": PERSONALITY + " Tu motor base es Qwen2.5 1.5B local, no entrenamiento nuevo.",
            "parameters": {"num_ctx": 2048, "num_predict": 384, "temperature": 0.2}, "stream": False})
        response.raise_for_status()
        if response.json().get("status") != "success":
            raise SystemExit("Ollama no confirmo la creacion del perfil.")
    print(f"Perfil {MODEL_PROFILE} creado sobre Qwen2.5 1.5B local; pesos reutilizados, sin API de pago.")


async def install() -> None:
    host = LocalBrainHost()
    try:
        if not await host.start():
            raise SystemExit("No se encontro un motor Ollama local disponible.")
        await create_profile()
    finally:
        await host.stop()


if __name__ == "__main__":
    asyncio.run(install())
