"""Dictado local Vosk: PCM acotado en RAM, sin red ni apertura del microfono OS."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any, Callable

from .audit import AuditStore
from .native_worker import run_native as _native
from .paths import data_root

SAMPLE_RATE = 16000
MAX_SECONDS = 15
MAX_PCM_BYTES = SAMPLE_RATE * MAX_SECONDS * 2
MAX_TRANSCRIPT = 2000
MODEL_NAME = "vosk-model-small-es-0.42"
MODEL_PATH = data_root() / ".models" / MODEL_NAME
TRANSCRIBE_PATH = "/api/v1/microphone/transcribe/"


def _load_model(path: Path) -> Any:
    """Importa DLLs y lee el modelo solo desde un worker, nunca lo descarga."""
    if not path.is_dir():
        raise FileNotFoundError("speech_model_missing")
    from vosk import Model, SetLogLevel

    SetLogLevel(-1)
    return Model(str(path))


def _decode(model: Any, pcm: bytes) -> str:
    """El reconocedor y sus buffers pertenecen a una sola invocacion nativa."""
    from vosk import KaldiRecognizer

    recognizer = KaldiRecognizer(model, SAMPLE_RATE)
    parts: list[str] = []

    def append(raw: str) -> None:
        data = json.loads(raw)
        text = data.get("text") if isinstance(data, dict) else None
        if not isinstance(text, str):
            raise ValueError("speech_invalid_result")
        parts.append(text.strip())

    for offset in range(0, len(pcm), 4000):
        if recognizer.AcceptWaveform(pcm[offset:offset + 4000]):
            append(recognizer.Result())
    append(recognizer.FinalResult())
    text = " ".join(part for part in parts if part)
    if len(text) > MAX_TRANSCRIPT:
        raise ValueError("speech_invalid_result")
    return text


class LocalSpeechInput:
    """Un dictado a la vez. Estado en cache; no graba ni guarda audio o texto."""

    def __init__(self, audit: AuditStore, *, enabled: bool | None = None,
                 model_path: Path = MODEL_PATH,
                 loader: Callable[..., Any] = _load_model,
                 decoder: Callable[..., str] = _decode) -> None:
        flag = os.getenv("THORN_MICROPHONE_ENABLED", "0")
        self.enabled = flag == "1" if enabled is None else enabled
        self.state = "invalid_configuration" if enabled is None and flag not in {"0", "1"} else "disabled"
        self.audit, self.model_path = audit, model_path
        self._loader, self._decoder = loader, decoder
        self._model: Any = None
        self._lock = asyncio.Lock()
        self._closed = False
        self.audit_errors = 0

    @property
    def status(self) -> dict[str, object]:
        """Disponibilidad del dictado, no estado fisico del dispositivo del usuario."""
        # NOTA DEL OPERADOR: Vosk recibe clips; el HUD controla cuando captura.
        # Tener el modelo listo no significa que el microfono este escuchando.
        return {"provider": "vosk", "state": "closed" if self._closed else self.state,
                "available": self.state == "ready" and not self._closed,
                "busy": self._lock.locked(), "cloud": False, "language": "es",
                "model": MODEL_NAME, "sample_rate": SAMPLE_RATE,
                "max_seconds": MAX_SECONDS, "max_bytes": MAX_PCM_BYTES,
                "requires_user_gesture": True, "continuous_listening": False,
                "audit_errors": self.audit_errors}

    async def start(self) -> None:
        """Modelo opcional: su ausencia no derriba MySQL, HUD ni voz de salida."""
        if not self.enabled or self.state == "invalid_configuration":
            return
        async with self._lock:
            if self._closed or self.state == "ready":
                return
            self.state = "loading"
            try:
                self._model = await _native(self._loader, self.model_path)
            except FileNotFoundError:
                self.state = "model_missing"
            except (ImportError, OSError):
                self.state = "engine_missing"
            except Exception:
                self.state = "model_invalid"
            else:
                self.state = "ready"

    async def _record(self, state: str, identity: str, size: int) -> None:
        try:
            async with asyncio.timeout(3):
                await self.audit.append("assistant.dictation", state, "Dictado local Vosk",
                                        {"request_id": identity, "audio_bytes": size,
                                         "model": MODEL_NAME})
        except Exception:
            self.audit_errors += 1

    async def transcribe(self, pcm: bytes, *, local_consent: bool, request_id: str) -> dict[str, object]:
        """PCM mono, signed int16 little-endian, 16 kHz; no ejecuta consultas."""
        if local_consent is not True:
            raise ValueError("microphone_local_consent_required")
        if not isinstance(pcm, bytes) or not 3200 <= len(pcm) <= MAX_PCM_BYTES or len(pcm) % 2:
            raise ValueError("microphone_invalid_audio")
        if self._lock.locked():
            raise RuntimeError("microphone_busy")
        async with self._lock:
            if self.state != "ready" or self._closed:
                raise RuntimeError("microphone_" + ("closed" if self._closed else self.state))
            await self._record("requested", request_id, len(pcm))
            try:
                text = await _native(self._decoder, self._model, pcm)
                if not isinstance(text, str) or len(text) > MAX_TRANSCRIPT:
                    raise ValueError("speech_invalid_result")
            except asyncio.CancelledError:
                await self._record("cancelled", request_id, len(pcm))
                raise
            except Exception:
                await self._record("failed", request_id, len(pcm))
                raise RuntimeError("microphone_transcription_failed") from None
            await self._record("transcribed", request_id, len(pcm))
            return {"text": text.strip(), "has_speech": bool(text.strip()), "provider": "vosk",
                    "cloud": False, "request_id": request_id}

    async def stop(self) -> None:
        """Espera reconocimiento activo y libera el modelo fuera del event loop."""
        self._closed = True
        async with self._lock:
            models, self._model = [self._model], None
            # clear realiza el ultimo decremento de referencia en el worker.
            await _native(models.clear)
