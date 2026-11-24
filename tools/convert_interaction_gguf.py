"""Convierte el modelo de interacción fusionado a GGUF para servirlo en Ollama (Fase 2).

Ollama 0.40 en Windows no ejecuta modelos importados desde safetensors (solo con
su motor MLX), así que se convierte antes con el conversor oficial de llama.cpp,
descargado aparte en .tools/ (ver docs/MODELO_DE_INTERACCION.md).

    .tools\\entrenamiento\\Scripts\\python.exe tools\\convert_interaction_gguf.py .models\\thorn-interaccion-lora-v3-fusionado
    ollama create thorn-interaccion:v3 -f .models\\thorn-interaccion-lora-v3-fusionado\\Modelfile

NOTA DEL OPERADOR: el conversor importa sentencepiece antes de comprobar si el
modelo trae tokenizer.model; si no lo trae, pasa al vocabulario BPE. Qwen2.5 no
lo trae, así que aquí solo se adelanta esa comprobación (mismo FileNotFoundError
que lanza el conversor) en vez de instalar otra dependencia en el entorno.
"""

from __future__ import annotations

import argparse
import importlib
import importlib.util
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
LLAMA_CPP = ROOT / ".tools" / "llama.cpp-b12048"


def _check_tokenizer_model_first() -> None:
    base = importlib.import_module("conversion.base")
    owner = next(c for c in vars(base).values() if isinstance(c, type) and "_create_vocab_sentencepiece" in vars(c))
    original = owner._create_vocab_sentencepiece

    def create(self):
        if not (self.dir_model / "tokenizer.model").is_file():
            raise FileNotFoundError(f"File not found: {self.dir_model / 'tokenizer.model'}")
        return original(self)

    owner._create_vocab_sentencepiece = create


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("modelo", type=Path, help="carpeta creada por train_interaction_model.py --fusionar")
    parser.add_argument("--llama-cpp", type=Path, default=LLAMA_CPP)
    parser.add_argument("--tipo", default="f16", choices=["f16", "bf16", "q8_0"])
    args = parser.parse_args()
    script = args.llama_cpp / "convert_hf_to_gguf.py"
    if not script.is_file() or not (args.modelo / "config.json").is_file():
        print(f"Falta {script} o el modelo fusionado en {args.modelo}.", file=sys.stderr)
        return 2
    # Solo archivos locales: el conversor no debe consultar Hugging Face.
    os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
    sys.path[:0] = [str(args.llama_cpp), str(args.llama_cpp / "gguf-py")]
    if importlib.util.find_spec("sentencepiece") is None:
        _check_tokenizer_model_first()
    out = args.modelo / f"modelo-{args.tipo}.gguf"
    sys.argv = [str(script), str(args.modelo), "--outtype", args.tipo, "--outfile", str(out)]
    spec = importlib.util.spec_from_file_location("convert_hf_to_gguf", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.main()
    # El evaluador y Thorn arman el prompt ChatML a mano (raw), así que la plantilla de
    # Ollama no interviene; temperatura 0 para que la misma frase dé la misma decisión.
    modelfile = args.modelo / ("Modelfile" if args.tipo == "f16" else f"Modelfile-{args.tipo}")
    modelfile.write_text(f"FROM ./{out.name}\nPARAMETER temperature 0\nPARAMETER stop <|im_end|>\n", encoding="utf-8")
    print(f"GGUF en {out}\nImportar: ollama create thorn-interaccion:<version> -f {modelfile}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
