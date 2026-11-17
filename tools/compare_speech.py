"""Compara dos modelos locales con WAV mono PCM16 de 16 kHz y referencias privadas."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
import time
import wave

import psutil

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.brain.commands import normalize
from core.voice.speech_input import MAX_SECONDS, MODEL_PATH, SAMPLE_RATE, _decode, _load_model


def edits(reference: list[str], actual: list[str]) -> int:
    row = list(range(len(actual) + 1))
    for i, expected in enumerate(reference, 1):
        next_row = [i]
        for j, heard in enumerate(actual, 1):
            next_row.append(min(row[j] + 1, next_row[-1] + 1, row[j - 1] + (expected != heard)))
        row = next_row
    return row[-1]


def measure(model_path: Path, manifest: Path) -> dict[str, object]:
    cases = json.loads(manifest.read_text(encoding="utf-8"))
    if not isinstance(cases, list) or not 1 <= len(cases) <= 100:
        raise ValueError("Se requieren de 1 a 100 muestras.")
    started = time.perf_counter()
    model = _load_model(model_path)
    load_ms = round((time.perf_counter() - started) * 1000)
    process = psutil.Process()
    rows = []
    for index, case in enumerate(cases, 1):
        reference = normalize(case["text"]).split()
        if not reference or len(reference) > 150:
            raise ValueError(f"Referencia inválida en muestra {index}")
        with wave.open(str(manifest.parent / case["wav"]), "rb") as clip:
            if ((clip.getnchannels(), clip.getsampwidth(), clip.getframerate()) != (1, 2, SAMPLE_RATE)
                    or not 1 <= clip.getnframes() <= SAMPLE_RATE * MAX_SECONDS):
                raise ValueError(f"WAV inválido en muestra {index}")
            pcm = clip.readframes(clip.getnframes())
        started = time.perf_counter()
        actual = normalize(_decode(model, pcm)).split()
        rows.append({"sample": index, "ms": round((time.perf_counter() - started) * 1000),
                     "edits": edits(reference, actual), "reference_words": len(reference)})
    memory = process.memory_info()
    return {"model": model_path.name, "load_ms": load_ms, "rss_mib": round(memory.rss / 1024**2, 1),
            "peak_rss_mib": round(memory.peak_wset / 1024**2, 1) if hasattr(memory, "peak_wset") else None,
            "wer": round(sum(row["edits"] for row in rows) / sum(row["reference_words"] for row in rows), 4),
            "samples": rows}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path, help='Lista JSON con objetos {"wav": "muestra.wav", "text": "lo dicho"}.')
    parser.add_argument("--candidate", type=Path, default=ROOT / ".models" / "vosk-model-es-0.42")
    parser.add_argument("--worker", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        print(json.dumps(measure(args.worker, args.manifest)))
    else:
        for path in (MODEL_PATH, args.candidate):
            subprocess.run([sys.executable, __file__, str(args.manifest), "--worker", str(path)], check=True)
