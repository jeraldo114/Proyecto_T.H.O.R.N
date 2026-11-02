"""Documentos locales en RAM, por sesion, con parser separado y vigilancia."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import re
import sys
from dataclasses import dataclass
from collections.abc import Callable
from pathlib import Path
from uuid import uuid4
import psutil

from .document_worker import MAX_PDF_BYTES
from .commands import normalize
from .paths import RESOURCE_ROOT, compiled

UPLOAD_PATH = "/api/v1/documents/upload/"


def question_excerpts(pages: list[dict[str, object]], prompt: str) -> list[dict[str, object]]:
    """Selecciona texto acotado por coincidencia local; conserva el numero de pagina."""
    words = set(re.findall(r"\w{3,}", normalize(prompt)))
    ranked = sorted(enumerate(pages), key=lambda item: (
        -len(words & set(re.findall(r"\w{3,}", normalize(str(item[1]["text"]))))), item[0]))
    selected: list[dict[str, object]] = []
    remaining = 3600
    for _, page in ranked:
        text = str(page["text"])[:min(1800, remaining)]
        if text:
            selected.append({"page": page["page"], "text": text})
            remaining -= len(text)
        if remaining == 0:
            break
    return sorted(selected, key=lambda page: page["page"])


async def parse_isolated(raw: bytes) -> dict[str, object]:
    """Un PDF no puede ocupar indefinidamente el loop ni un hilo de inferencia."""
    command = [sys.executable, "--document-worker"] if compiled() else [sys.executable, "-m", "core.document_worker"]
    # NOTA DEL OPERADOR: el lector recibe solo bytes PDF, sin .env ni claves.
    environment = {name: value for name, value in os.environ.items()
                   if name.upper() in {"SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP", "LOCALAPPDATA"}}
    if not compiled():
        environment["PYTHONPATH"] = str(RESOURCE_ROOT)
    options = {"creationflags": 0x08000000} if os.name == "nt" else {}
    process = await asyncio.create_subprocess_exec(*command, stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        cwd=str(RESOURCE_ROOT), env=environment, **options)
    async def exchange() -> bytes:
        process.stdin.write(raw)
        await process.stdin.drain()
        process.stdin.close()
        output = bytearray()
        while chunk := await process.stdout.read(16384):
            output.extend(chunk)
            if len(output) > 256 * 1024:
                raise ValueError("document_output_limit")
        await process.wait()
        return bytes(output)
    async def monitor() -> None:
        child = psutil.Process(process.pid)
        while process.returncode is None:
            try:
                rss = await asyncio.to_thread(lambda: child.memory_info().rss)
            except psutil.NoSuchProcess:
                return
            if rss > 256 * 1024 * 1024:
                raise ValueError("document_memory_limit")
            await asyncio.sleep(0.05)
    operation = asyncio.create_task(exchange())
    watchdog = asyncio.create_task(monitor())
    try:
        async with asyncio.timeout(15):
            done, _ = await asyncio.wait({operation, watchdog}, return_when=asyncio.FIRST_COMPLETED)
            if watchdog in done:
                await watchdog
            payload = await operation
        value = json.loads(payload)
        if "error" in value:
            raise ValueError(value["error"])
        return value["document"]
    except TimeoutError:
        raise ValueError("document_timeout") from None
    finally:
        if process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
        await process.wait()
        for task in (operation, watchdog):
            if not task.done():
                task.cancel()
        await asyncio.gather(operation, watchdog, return_exceptions=True)


@dataclass(slots=True)
class Document:
    owner: str
    name: str
    raw: bytes
    result: dict[str, object]


class DocumentService:
    """Hasta dos documentos por sesion y 32 MiB globales; no persiste originales."""

    def __init__(self) -> None:
        self.items: dict[str, Document] = {}
        self.lock = asyncio.Lock()

    def release(self, owner: str) -> None:
        self.items = {key: value for key, value in self.items.items() if value.owner != owner}

    async def upload(self, owner: str, raw: bytes, name: str, *, alive: Callable[[], bool] | None = None) -> dict[str, object]:
        if not raw or len(raw) > MAX_PDF_BYTES or not raw.startswith(b"%PDF-"):
            raise ValueError("document_invalid_pdf")
        if (not isinstance(name, str) or not re.fullmatch(r"[^/\\\x00-\x1f]{1,100}\.pdf", name, re.I)):
            raise ValueError("document_invalid_name")
        if self.lock.locked():
            raise RuntimeError("document_busy")
        async with self.lock:
            if sum(item.owner == owner for item in self.items.values()) >= 2:
                raise ValueError("document_session_limit")
            if sum(len(item.raw) for item in self.items.values()) + len(raw) > 32 * 1024 * 1024:
                raise ValueError("document_storage_limit")
            result = await parse_isolated(raw)
            digest = await asyncio.to_thread(lambda: hashlib.sha256(raw).hexdigest())
            if alive is not None and not alive():
                raise RuntimeError("interaction_cancelled")
            identity = uuid4().hex
            self.items[identity] = Document(owner, name, raw, result)
            return {"id": identity, "name": name, "size_bytes": len(raw),
                    "sha256": digest, **result, "durable": False}

    def get(self, owner: str, identity: str) -> Document:
        item = self.items.get(identity)
        if item is None or item.owner != owner:
            raise ValueError("document_not_found")
        return item

    def add_ocr(self, owner: str, identity: str, page: object, text: object,
                confidence: object) -> dict[str, object]:
        """Adjunta texto OCR no confiable; nunca cambia el PDF original."""
        item = self.get(owner, identity)
        if (type(page) is not int or not 1 <= page <= item.result["page_count"]
                or not isinstance(text, str) or not 1 <= len(text.strip()) <= 3000
                or type(confidence) not in {int, float} or not math.isfinite(confidence)
                or not 0 <= confidence <= 100):
            raise ValueError("document_invalid_ocr")
        pages = [p for p in item.result["pages"] if p["page"] != page]
        if sum(len(p["text"]) for p in pages) + len(text) > 6000:
            raise ValueError("document_ocr_text_limit")
        pages.append({"page": page, "text": text.strip(), "source": "ocr", "confidence": round(confidence, 1)})
        item.result.update(pages=sorted(pages, key=lambda p: p["page"]), ocr=True, text_available=True)
        return {"id": identity, "name": item.name, "size_bytes": len(item.raw), **item.result, "durable": False}
