"""Sintesis remota asincrona. Sin microfono, clones, archivos ni reintentos de pago."""

from __future__ import annotations

import asyncio
import os
import re
import time
from dataclasses import dataclass, field
from typing import Self

import httpx

from .audit import AuditStore

API_ORIGIN = "https://api.elevenlabs.io"
MAX_TEXT = 2000
MAX_AUDIO = 2 * 1024 * 1024
MODELS = {"eleven_flash_v2_5", "eleven_multilingual_v2"}


def valid_voice_id(value: object) -> bool:
    """Evita rutas, URLs y parametros inyectados en el identificador remoto."""
    return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,80}", value) is not None


@dataclass(frozen=True, slots=True)
class VoiceConfig:
    """Secretos solo en servidor; repr nunca incluye la API key."""

    enabled: bool = False
    api_key: str = field(default="", repr=False)
    voice_id: str = ""
    model: str = "eleven_flash_v2_5"
    invalid: bool = False

    def __post_init__(self) -> None:
        if (type(self.enabled) is not bool or self.model not in MODELS
                or not isinstance(self.api_key, str) or len(self.api_key) > 512
                or (self.api_key and not re.fullmatch(r"[A-Za-z0-9_-]+", self.api_key))
                or (self.voice_id and not valid_voice_id(self.voice_id))):
            raise ValueError("voice_invalid_configuration")

    @classmethod
    def from_env(cls) -> Self:
        """Una opcion de voz invalida no derriba el nucleo local."""
        try:
            enabled = os.getenv("THORN_VOICE_ENABLED", "0")
            if enabled not in {"0", "1"}:
                raise ValueError("voice_invalid_configuration")
            return cls(enabled=enabled == "1", api_key=os.getenv("ELEVENLABS_API_KEY", ""),
                       voice_id=os.getenv("ELEVENLABS_VOICE_ID", ""),
                       model=os.getenv("ELEVENLABS_MODEL_ID", "eleven_flash_v2_5"))
        except ValueError:
            return cls(invalid=True)


class ElevenLabsVoice:
    """Una peticion a la vez, 45 s totales y 2 MiB de audio como maximo."""

    def __init__(self, audit: AuditStore, config: VoiceConfig, *,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.audit, self.config = audit, config
        self.client = httpx.AsyncClient(base_url=API_ORIGIN, trust_env=False,
                                       follow_redirects=False, transport=transport,
                                       timeout=httpx.Timeout(30, connect=5))
        self._lock = asyncio.Lock()
        self._closed = False
        self._last_started = float("-inf")
        self._provider_state = "configured"
        self.audit_errors = 0

    @property
    def status(self) -> dict[str, object]:
        """Estado en cache: arrancar o consultar el HUD no consume creditos."""
        config = self.config
        state = ("closed" if self._closed else "invalid_configuration" if config.invalid else
                 "disabled" if not config.enabled else "missing_key" if not config.api_key else
                 "missing_voice" if not config.voice_id else self._provider_state)
        return {"provider": "elevenlabs", "state": state,
                "available": state in {"configured", "ready"}, "busy": self._lock.locked(),
                "can_request": state in {"configured", "ready", "provider_unavailable", "rate_limited"},
                "model": config.model, "cloud": True, "microphone": False,
                "requires_consent": True, "max_characters": MAX_TEXT,
                "audit_errors": self.audit_errors}

    async def _record(self, state: str, request_id: str, count: int) -> None:
        try:
            async with asyncio.timeout(3):
                await self.audit.append("assistant.speech", state, "Sintesis de voz ElevenLabs",
                                        {"request_id": request_id, "characters": count,
                                         "model": self.config.model})
        except Exception:
            self.audit_errors += 1

    async def speak(self, text: str, *, cloud_consent: bool, request_id: str) -> bytes:
        """Envia solo el texto elegido. Mantiene el MP3 acotado en memoria RAM."""
        if cloud_consent is not True:
            raise ValueError("voice_cloud_consent_required")
        if not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT:
            raise ValueError("voice_invalid_text")
        if self._lock.locked():
            raise RuntimeError("voice_busy")
        async with self._lock:
            state = self.status["state"]
            if state not in {"configured", "ready", "provider_unavailable", "rate_limited"}:
                raise RuntimeError("voice_" + str(state))
            if time.monotonic() - self._last_started < 2:
                raise RuntimeError("voice_busy")
            self._last_started = time.monotonic()
            text = text.strip()
            await self._record("requested", request_id, len(text))
            body: dict[str, object] = {
                "text": text, "model_id": self.config.model,
                "voice_settings": {"stability": 0.65, "similarity_boost": 0.75,
                                   "style": 0.0, "use_speaker_boost": False, "speed": 0.97},
            }
            if self.config.model == "eleven_flash_v2_5":
                body["language_code"] = "es"
            try:
                async with asyncio.timeout(45):
                    async with self.client.stream(
                        "POST", f"/v1/text-to-speech/{self.config.voice_id}/stream",
                        params={"output_format": "mp3_44100_128"}, json=body,
                        headers={"xi-api-key": self.config.api_key, "Accept": "audio/mpeg"},
                    ) as response:
                        if response.status_code in {401, 403}:
                            raise RuntimeError("voice_auth_failed")
                        if response.status_code == 429:
                            raise RuntimeError("voice_rate_limited")
                        if response.status_code == 404:
                            raise RuntimeError("voice_not_found")
                        if response.status_code != 200:
                            raise RuntimeError("voice_provider_unavailable")
                        if response.headers.get("content-type", "").split(";")[0] != "audio/mpeg":
                            raise RuntimeError("voice_invalid_audio")
                        audio = bytearray()
                        async for chunk in response.aiter_bytes():
                            if len(audio) + len(chunk) > MAX_AUDIO:
                                raise RuntimeError("voice_invalid_audio")
                            audio.extend(chunk)
                        if not audio:
                            raise RuntimeError("voice_invalid_audio")
            except asyncio.CancelledError:
                await self._record("cancelled", request_id, len(text))
                raise
            except (httpx.HTTPError, TimeoutError):
                self._provider_state = "provider_unavailable"
                await self._record("failed", request_id, len(text))
                raise RuntimeError("voice_provider_unavailable") from None
            except RuntimeError as exc:
                self._provider_state = str(exc).removeprefix("voice_")
                await self._record("failed", request_id, len(text))
                raise
            self._provider_state = "ready"
            await self._record("synthesized", request_id, len(text))
            return bytes(audio)

    async def stop(self) -> None:
        """Impide nuevas solicitudes y cierra TLS tras terminar la solicitud activa."""
        self._closed = True
        async with self._lock:
            await self.client.aclose()
