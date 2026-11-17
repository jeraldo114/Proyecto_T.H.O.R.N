"""Mide la deteccion de «Thorn» con tus grabaciones: aciertos, falsas alarmas y tiempo.

Manifiesto JSON privado (fuera de Git, por ejemplo en .artifacts/):
    [{"wav": "thorn_abre.wav", "wake": true}, {"wav": "charla.wav", "wake": false}]
Los WAV deben ser mono PCM16 a 16 kHz, de hasta 15 s. Graba frases reales:
positivas («Thorn, …», «Oye Thorn, …») y negativas que empiecen parecido
(«toma nota», «torre», «son las tres», «por favor…»).

    python tools/evaluate_wake.py .artifacts/nombre.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time
import wave

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.commands import WAKE_ALIASES, normalize  # noqa: E402
from core.speech_input import (MAX_SECONDS, MODEL_PATH, SAMPLE_RATE, _decode, _detect_wake,  # noqa: E402
                               _load_model, supports_grammar)


def read_pcm(path: Path) -> bytes:
    with wave.open(str(path), "rb") as wav:
        if (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) != (1, 2, SAMPLE_RATE):
            raise ValueError(f"{path.name}: se requiere WAV mono PCM16 de 16 kHz")
        if wav.getnframes() > SAMPLE_RATE * MAX_SECONDS:
            raise ValueError(f"{path.name}: supera {MAX_SECONDS} s")
        return wav.readframes(wav.getnframes())


def alias_match(text: str) -> bool:
    """Respaldo anterior: texto libre y lista de alias al inicio."""
    words = normalize(text).split()
    if words and words[0] in {"oye", "hola", "hey"}:
        words = words[1:]
    return bool(words) and words[0] in {*WAKE_ALIASES, "tom", "ton"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--model", type=Path, default=MODEL_PATH, help="modelo Vosk con grafo dinamico")
    args = parser.parse_args()
    if not supports_grammar(args.model):
        print(f"{args.model.name} no admite gramatica en vivo; usa el modelo pequeno.", file=sys.stderr)
        return 2
    cases = json.loads(args.manifest.read_text(encoding="utf-8"))
    if not isinstance(cases, list) or not 1 <= len(cases) <= 200:
        raise ValueError("Se requieren de 1 a 200 muestras.")
    model = _load_model(args.model)
    totals = {"grammar": [0, 0, 0, 0], "aliases": [0, 0, 0, 0]}  # aciertos, omisiones, falsas, rechazos
    elapsed = []
    for case in cases:
        pcm = read_pcm(args.manifest.parent / case["wav"])
        started = time.perf_counter()
        grammar = _detect_wake(model, pcm) is not None
        elapsed.append((time.perf_counter() - started) * 1000)
        aliases = alias_match(_decode(model, pcm))
        for name, detected in (("grammar", grammar), ("aliases", aliases)):
            index = (0 if detected else 1) if case["wake"] else (2 if detected else 3)
            totals[name][index] += 1
        print(f"{case['wav']:32} esperado={'si' if case['wake'] else 'no':2} gramatica={'si' if grammar else 'no':2} "
              f"alias={'si' if aliases else 'no'}")
    for name, (hits, misses, false_alarms, rejections) in totals.items():
        positives, negatives = hits + misses, false_alarms + rejections
        print(f"{name:8} detecta {hits}/{positives} · falsas alarmas {false_alarms}/{negatives}")
    print(f"gramatica: mediana {sorted(elapsed)[len(elapsed) // 2]:.0f} ms por clip")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
