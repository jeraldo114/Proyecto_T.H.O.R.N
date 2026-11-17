"""Genera una frase hablada en WAV con Piper local, para las pruebas de voz en navegador.

Sin servicios externos ni descargas: usa el modelo Piper ya instalado en .models/.

    python tools/make_voice_fixture.py
    python tools/make_voice_fixture.py --texto "Thorn, abre el sistema." --salida .artifacts/otra.wav

El archivo queda en .artifacts/ (fuera de Git). Úsalo con THORN_VOICE_FIXTURE_WAV.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.voice.local_voice import MODEL_PATH, load_voice, synthesize  # noqa: E402

DEFAULT_TEXT = "Thorn, esta es una consulta de prueba para la conversación."


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--texto", default=DEFAULT_TEXT, help="frase que se sintetiza")
    parser.add_argument("--salida", type=Path, default=ROOT / ".artifacts" / "frase-prueba.wav")
    args = parser.parse_args()
    if not args.texto.strip() or len(args.texto) > 500:
        parser.error("La frase debe tener entre 1 y 500 caracteres.")
    try:
        voice = load_voice(MODEL_PATH)
    except FileNotFoundError:
        print("Falta el modelo Piper. Instálalo con tools/install_voice_model.py.", file=sys.stderr)
        return 2
    args.salida.parent.mkdir(parents=True, exist_ok=True)
    args.salida.write_bytes(synthesize(voice, args.texto.strip()))
    print(f"Audio de prueba: {args.salida}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
