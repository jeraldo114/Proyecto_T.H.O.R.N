"""Arranque opcional de Ollama instalado: solo loopback, sin nube ni descargas."""

from __future__ import annotations

import asyncio
import os
import subprocess
from pathlib import Path

import httpx


class LocalBrainHost:
    """Cierra solo el proceso que haya creado, nunca una instancia ajena."""

    def __init__(self) -> None:
        self.process: asyncio.subprocess.Process | None = None

    async def start(self) -> bool:
        async with httpx.AsyncClient(base_url="http://127.0.0.1:11434", trust_env=False, follow_redirects=False, timeout=1) as client:
            async def ready() -> bool:
                try:
                    response = await client.get("/api/tags")
                    return response.status_code == 200 and isinstance(response.json().get("models"), list)
                except (httpx.HTTPError, ValueError, AttributeError):
                    return False
            if await ready():
                return True
            if os.name != "nt":
                return False
            executable = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe"
            if not await asyncio.to_thread(executable.is_file):
                return False
            allowed = {"SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP", "USERPROFILE", "LOCALAPPDATA", "APPDATA", "HOMEDRIVE", "HOMEPATH"}
            environment = {key: value for key, value in os.environ.items() if key.upper() in allowed}
            environment.update({"OLLAMA_NO_CLOUD": "1", "OLLAMA_HOST": "127.0.0.1:11434",
                "OLLAMA_NUM_PARALLEL": "1", "OLLAMA_MAX_LOADED_MODELS": "1", "OLLAMA_CONTEXT_LENGTH": "2048"})
            try:
                self.process = await asyncio.create_subprocess_exec(str(executable), "serve", env=environment,
                    stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NO_WINDOW)
                async with asyncio.timeout(8):
                    while self.process.returncode is None:
                        if await ready():
                            return True
                        await asyncio.sleep(0.2)
            except asyncio.CancelledError:
                await self.stop()
                raise
            except (OSError, TimeoutError):
                pass
            await self.stop()
            return False

    async def stop(self) -> None:
        process, self.process = self.process, None
        if process is None or process.returncode is not None:
            return
        try:
            process.terminate()
        except ProcessLookupError:
            return
        try:
            async with asyncio.timeout(3):
                await process.wait()
        except TimeoutError:
            process.kill(); await process.wait()
