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
PRECISE_MODEL_NAME = "vosk-model-es-0.42"
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


# NOTA DEL OPERADOR: «Thorn» no es una palabra del español. En texto libre Vosk
# la cambia por «tom», «son», «don» o la omite. Con una gramatica cerrada solo
# puede elegir entre el nombre, palabras comunes que suenan parecido y [unk].
# Las palabras de distraccion evitan que «toma», «torre» o «por» se fuercen a
# «thorn». Medido con voz sintetizada: 10/10 detecciones y 0/16 falsas alarmas.
# Con tu voz real, mide con tools/evaluate_wake.py antes de cambiar esta lista.
WAKE_WORD = "thorn"
WAKE_PREFIXES = frozenset({"oye", "hola", "hey"})
WAKE_DISTRACTORS = (
    "que qué de no a la el es y en lo un por me una te los se con para mi esta está si sí bien pero yo eso "
    "las su tu aquí del al como cómo le más esto ya todo vamos muy hay ahora algo estoy tengo nos nada "
    "cuando este así puedo quiero son tiene dónde toma torre tornillo tono corre don pon otra abre dime revisa "
    # Empiezan como «Thorn»: sin ellas «Tormenta eléctrica…» activaba el nombre.
    "tormenta tortuga torta tornado torneo tortilla tomate tomar tomo tonto torcer"
).split()
WAKE_GRAMMAR = json.dumps([WAKE_WORD, *(f"{prefix} {WAKE_WORD}" for prefix in sorted(WAKE_PREFIXES)),
                           *WAKE_DISTRACTORS, "[unk]"], ensure_ascii=False)
WAKE_MAX_START = 1.8  # segundos: el nombre debe abrir la frase, no aparecer en medio
WAKE_MIN_CONFIDENCE = 0.5


def supports_grammar(path: Path) -> bool:
    """Solo los modelos con grafo dinamico (los pequenos) aceptan gramatica en vivo."""
    return (path / "graph" / "HCLr.fst").is_file() and (path / "graph" / "Gr.fst").is_file()


def wake_end(words: list[dict[str, Any]]) -> float | None:
    """Fin del nombre si abre la frase (con «oye»/«hola» delante como mucho)."""
    spoken = [item for item in words if item.get("word") != "[unk]"][:2]
    if spoken and spoken[0].get("word") in WAKE_PREFIXES:
        spoken = spoken[1:]
    if not spoken:
        return None
    first = spoken[0]
    start, end, confidence = first.get("start"), first.get("end"), first.get("conf", 1.0)
    if (first.get("word") != WAKE_WORD or not all(isinstance(value, (int, float)) for value in (start, end, confidence))
            or start > WAKE_MAX_START or end <= start or confidence < WAKE_MIN_CONFIDENCE):
        return None
    return float(end)


def _detect_wake(model: Any, pcm: bytes) -> float | None:
    """Busca el nombre con gramatica cerrada; no produce una transcripcion del resto."""
    from vosk import KaldiRecognizer

    recognizer = KaldiRecognizer(model, SAMPLE_RATE, WAKE_GRAMMAR)
    recognizer.SetWords(True)
    words: list[dict[str, Any]] = []

    def append(raw: str) -> None:
        data = json.loads(raw)
        result = data.get("result", []) if isinstance(data, dict) else None
        if not isinstance(result, list) or not all(isinstance(item, dict) for item in result):
            raise ValueError("speech_invalid_result")
        words.extend(result)

    # Basta con los primeros segundos: el nombre debe abrir la frase.
    limit = min(len(pcm), int((WAKE_MAX_START + 1.2) * SAMPLE_RATE) * 2)
    for offset in range(0, limit, 4000):
        if recognizer.AcceptWaveform(pcm[offset:min(offset + 4000, limit)]):
            append(recognizer.Result())
    append(recognizer.FinalResult())
    return wake_end(words)


class LocalSpeechInput:
    """Un dictado a la vez. Estado en cache; no graba ni guarda audio o texto."""

    def __init__(self, audit: AuditStore, *, enabled: bool | None = None,
                 model_path: Path | None = None,
                 loader: Callable[..., Any] = _load_model,
                 decoder: Callable[..., str] = _decode,
                 wake_model_path: Path = MODEL_PATH,
                 wake_detector: Callable[..., float | None] = _detect_wake,
                 grammar_check: Callable[[Path], bool] = supports_grammar) -> None:
        flag = os.getenv("THORN_MICROPHONE_ENABLED", "0")
        self.enabled = flag == "1" if enabled is None else enabled
        self.state = "invalid_configuration" if enabled is None and flag not in {"0", "1"} else "disabled"
        selected = os.getenv("THORN_SPEECH_MODEL", MODEL_NAME)
        if model_path is None:
            if selected not in {MODEL_NAME, PRECISE_MODEL_NAME}:
                self.state = "invalid_configuration"
                selected = MODEL_NAME
            model_path = MODEL_PATH.with_name(selected)
        self.audit, self.model_path = audit, model_path
        self._loader, self._decoder = loader, decoder
        self._model: Any = None
        # El nombre se busca con el modelo pequeno aunque el dictado use el grande.
        self.wake_model_path, self._wake_detector, self._grammar_check = wake_model_path, wake_detector, grammar_check
        self._wake_model: Any = None
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
                "model": self.model_path.name, "sample_rate": SAMPLE_RATE,
                "max_seconds": MAX_SECONDS, "max_bytes": MAX_PCM_BYTES,
                "requires_user_gesture": True, "continuous_listening": False,
                "wake_detection": "grammar" if self._wake_model is not None else "aliases",
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
                await self._load_wake()

    async def _load_wake(self) -> None:
        """Detector opcional: si falta, el HUD reconoce el nombre por alias como antes."""
        path = self.wake_model_path
        try:
            if not await asyncio.to_thread(self._grammar_check, path):
                return
            self._wake_model = self._model if path == self.model_path else await _native(self._loader, path)
        except Exception:
            self._wake_model = None

    async def _record(self, state: str, identity: str, size: int, mode: str = "dictation") -> None:
        try:
            async with asyncio.timeout(3):
                await self.audit.append("assistant.dictation", state, "Dictado local Vosk",
                                        {"request_id": identity, "audio_bytes": size,
                                         "model": self.model_path.name, "mode": mode})
        except Exception:
            self.audit_errors += 1

    async def transcribe(self, pcm: bytes, *, local_consent: bool, request_id: str,
                         mode: str = "dictation") -> dict[str, object]:
        """PCM mono, signed int16 little-endian, 16 kHz; no ejecuta consultas.

        mode="wake": confirma primero «Thorn» con gramatica cerrada y solo transcribe lo
        dicho despues. Sin nombre no transcribe nada: la charla ambiental no se convierte en texto.
        """
        if local_consent is not True:
            raise ValueError("microphone_local_consent_required")
        if mode not in {"dictation", "wake"}:
            raise ValueError("microphone_invalid_mode")
        if not isinstance(pcm, bytes) or not 3200 <= len(pcm) <= MAX_PCM_BYTES or len(pcm) % 2:
            raise ValueError("microphone_invalid_audio")
        if self._lock.locked():
            raise RuntimeError("microphone_busy")
        async with self._lock:
            if self.state != "ready" or self._closed:
                raise RuntimeError("microphone_" + ("closed" if self._closed else self.state))
            await self._record("requested", request_id, len(pcm), mode)
            wake: bool | None = None
            try:
                audio = pcm
                if mode == "wake" and self._wake_model is not None:
                    end = await _native(self._wake_detector, self._wake_model, pcm)
                    wake = end is not None
                    audio = pcm[min(len(pcm), int(end * SAMPLE_RATE) * 2):] if end is not None else b""
                text = await _native(self._decoder, self._model, audio) if len(audio) >= 3200 else ""
                if not isinstance(text, str) or len(text) > MAX_TRANSCRIPT:
                    raise ValueError("speech_invalid_result")
            except asyncio.CancelledError:
                await self._record("cancelled", request_id, len(pcm), mode)
                raise
            except Exception:
                await self._record("failed", request_id, len(pcm), mode)
                raise RuntimeError("microphone_transcription_failed") from None
            await self._record("transcribed", request_id, len(pcm), mode)
            # wake=None: el servidor no pudo decidir y el HUD usa sus alias de respaldo.
            return {"text": text.strip(), "has_speech": bool(text.strip()), "provider": "vosk",
                    "cloud": False, "request_id": request_id, "wake": wake}

    async def stop(self) -> None:
        """Espera reconocimiento activo y libera el modelo fuera del event loop."""
        self._closed = True
        async with self._lock:
            models, self._model, self._wake_model = [self._model, self._wake_model], None, None
            # clear realiza el ultimo decremento de referencia en el worker.
            await _native(models.clear)
