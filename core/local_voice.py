"""Voz Piper local: sin API keys, cuotas, audio remoto ni descargas al arrancar."""

from __future__ import annotations

import asyncio
import io
import json
import os
import wave
from pathlib import Path
from typing import Any, Callable

from .audit import AuditStore
from .native_worker import run_native
from .paths import data_root

MODEL_NAME = "es_MX-ald-medium"
MODEL_DIR = data_root() / ".models" / "piper-es_MX-ald-medium"
MODEL_PATH = MODEL_DIR / (MODEL_NAME + ".onnx")
MAX_TEXT = 2000
MAX_AUDIO = 8 * 1024 * 1024


def load_voice(path: Path) -> Any:
    """Imports y modelo ONNX solo en el worker. El motor usa CPU, no CUDA."""
    if not path.is_file() or not path.with_suffix(".onnx.json").is_file():
        raise FileNotFoundError("voice_model_missing")
    import onnxruntime as ort
    from piper import PiperVoice
    from piper.config import PiperConfig
    config = PiperConfig.from_dict(json.loads(path.with_suffix(".onnx.json").read_text(encoding="utf-8")))
    config.espeak_voice = "es-419"
    options = ort.SessionOptions()
    # NOTA DEL OPERADOR: Piper comparte CPU con el resto de T.H.O.R.N.
    # Limitar los hilos evita que una frase monopolice todos los nucleos.
    options.intra_op_num_threads = 2; options.inter_op_num_threads = 1
    options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    return PiperVoice(config=config, session=ort.InferenceSession(str(path), sess_options=options,
        providers=["CPUExecutionProvider"]), download_dir=path.parent)


class BoundedAudio(io.BytesIO):
    """Un texto largo nunca reserva un WAV ilimitado."""
    def write(self, data: bytes) -> int:
        if self.tell() + len(data) > MAX_AUDIO:
            raise ValueError("voice_audio_too_large")
        return super().write(data)


def synthesize(voice: Any, text: str) -> bytes:
    """Genera WAV mono en RAM. Inferencia y conversion ocurren fuera de HTTP."""
    from piper import SynthesisConfig
    output = BoundedAudio()
    with wave.open(output, "wb") as wav:
        voice.synthesize_wav(text, wav, syn_config=SynthesisConfig(length_scale=1.03, noise_scale=0.5, noise_w_scale=0.7))
    audio = output.getvalue()
    if len(audio) < 46 or not audio.startswith(b"RIFF") or audio[8:12] != b"WAVE":
        raise ValueError("voice_invalid_audio")
    return audio


class LocalVoice:
    """Modelo residente y una sintesis a la vez; cancelar espera el worker nativo."""

    content_type = "audio/wav"

    def __init__(self, audit: AuditStore, *, enabled: bool | None = None, model_path: Path = MODEL_PATH,
                 loader: Callable[..., Any] = load_voice, synthesizer: Callable[..., bytes] = synthesize) -> None:
        flag = os.getenv("THORN_VOICE_ENABLED", "0")
        self.enabled = flag == "1" if enabled is None else enabled
        self.state = "invalid_configuration" if enabled is None and flag not in {"0", "1"} else "disabled"
        self.audit, self.model_path = audit, model_path
        self.loader, self.synthesizer = loader, synthesizer
        self.model: Any = None
        self.lock = asyncio.Lock()
        self.closed = False
        self.audit_errors = 0

    @property
    def status(self) -> dict[str, object]:
        state = "closed" if self.closed else self.state
        return {"provider": "piper", "state": state, "available": state == "ready",
                "can_request": state == "ready", "busy": self.lock.locked(), "cloud": False,
                "microphone": False, "requires_consent": False, "model": MODEL_NAME,
                "language": "es-MX", "max_characters": MAX_TEXT, "content_type": self.content_type,
                "audit_errors": self.audit_errors}

    async def start(self) -> None:
        if not self.enabled or self.state == "invalid_configuration":
            return
        async with self.lock:
            if self.closed or self.model is not None:
                return
            self.state = "loading"
            try:
                self.model = await run_native(self.loader, self.model_path)
            except FileNotFoundError:
                self.state = "model_missing"
            except (ImportError, OSError):
                self.state = "engine_missing"
            except Exception:
                self.state = "model_invalid"
            else:
                self.state = "ready"

    async def record(self, state: str, identity: str, characters: int) -> None:
        try:
            async with asyncio.timeout(2):
                await self.audit.append("assistant.speech.local", state, "Sintesis de voz local Piper",
                    {"request_id": identity, "characters": characters, "model": MODEL_NAME})
        except Exception:
            self.audit_errors += 1

    async def speak(self, text: str, *, cloud_consent: bool, request_id: str) -> bytes:
        """La autorizacion de nube no se reutiliza para elegir otro proveedor."""
        if cloud_consent is not False:
            raise ValueError("voice_local_consent_required")
        if not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT:
            raise ValueError("voice_invalid_text")
        if self.lock.locked():
            raise RuntimeError("voice_busy")
        async with self.lock:
            if self.closed or self.state != "ready":
                raise RuntimeError("voice_" + ("closed" if self.closed else self.state))
            await self.record("started", request_id, len(text))
            try:
                audio = await run_native(self.synthesizer, self.model, text.strip())
                if not isinstance(audio, bytes) or not 46 <= len(audio) <= MAX_AUDIO or not audio.startswith(b"RIFF"):
                    raise ValueError("invalid_audio")
            except asyncio.CancelledError:
                await self.record("cancelled", request_id, len(text))
                raise
            except Exception:
                await self.record("failed", request_id, len(text))
                raise RuntimeError("voice_synthesis_failed") from None
            await self.record("synthesized", request_id, len(text))
            return audio

    async def stop(self) -> None:
        self.closed = True
        async with self.lock:
            holder, self.model = [self.model], None
            await run_native(holder.clear)
        self.state = "closed"
