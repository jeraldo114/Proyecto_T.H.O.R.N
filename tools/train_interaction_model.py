"""Entrena con LoRA el modelo de interacción propio de Thorn (Fase 2).

Parte de un modelo base abierto (por defecto Qwen2.5-0.5B-Instruct, Apache-2.0)
y le enseña, con los datos de tools/generate_interaction_data.py, a devolver
decisiones del esquema core/brain/interaction_schema.py.

LoRA no modifica los pesos del modelo base: entrena unas matrices pequeñas
(«adaptador», unos MB) que se suman a algunas capas. Por eso cabe en 4 GB de VRAM.

Requiere el entorno de entrenamiento separado (no el del runtime):
    .tools\\entrenamiento\\Scripts\\python.exe tools\\train_interaction_model.py
    .tools\\entrenamiento\\Scripts\\python.exe tools\\train_interaction_model.py --fusionar

Salidas en .models/ (fuera de Git):
    thorn-interaccion-lora/            adaptador con mejor resultado en dev
    thorn-interaccion-lora-fusionado/  modelo completo (con --fusionar) y Modelfile para Ollama
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import random
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.brain.interaction_schema import TRAINED_SYSTEM, validate  # noqa: E402

DEFAULT_BASE = "Qwen/Qwen2.5-0.5B-Instruct"
LORA_TARGETS = ["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"]


def messages(text: str) -> list[dict[str, str]]:
    return [{"role": "system", "content": TRAINED_SYSTEM}, {"role": "user", "content": text}]


def answer(target: dict[str, object]) -> str:
    """Forma canónica de la respuesta: JSON compacto, mismo orden de claves siempre."""
    clean = validate(target)
    return json.dumps({"say": clean["say"], "actions": clean["actions"], "escalate": clean["escalate"]},
                      ensure_ascii=False, separators=(",", ":"))


def load(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def encode(tokenizer, row: dict[str, object], max_len: int) -> dict[str, list[int]] | None:
    """Solo la respuesta cuenta para la pérdida: el modelo no aprende a repetir la pregunta."""
    prompt = tokenizer.apply_chat_template(messages(row["text"]), tokenize=False, add_generation_prompt=True)
    prompt_ids = tokenizer(prompt, add_special_tokens=False)["input_ids"]
    answer_ids = tokenizer(answer(row["target"]) + tokenizer.eos_token, add_special_tokens=False)["input_ids"]
    ids = prompt_ids + answer_ids
    if len(ids) > max_len:
        return None
    return {"input_ids": ids, "labels": [-100] * len(prompt_ids) + answer_ids}


def batches(rows: list[dict[str, list[int]]], size: int, pad: int, rng: random.Random | None):
    order = list(range(len(rows)))
    if rng is not None:
        rng.shuffle(order)
    for start in range(0, len(order), size):
        chunk = [rows[i] for i in order[start:start + size]]
        width = max(len(item["input_ids"]) for item in chunk)
        yield (
            [item["input_ids"] + [pad] * (width - len(item["input_ids"])) for item in chunk],
            [item["labels"] + [-100] * (width - len(item["labels"])) for item in chunk],
            [[1] * len(item["input_ids"]) + [0] * (width - len(item["input_ids"])) for item in chunk],
        )


def exact_match(model, tokenizer, rows: list[dict[str, object]], torch) -> tuple[float, float]:
    """Exactitud y tasa de salidas inválidas con generación greedy, como en uso real."""
    from tools.evaluate_interaction import same  # misma comparación que el evaluador

    model.eval()
    hits = invalid = 0
    with torch.no_grad():
        for row in rows:
            prompt = tokenizer.apply_chat_template(messages(row["text"]), tokenize=False, add_generation_prompt=True)
            inputs = tokenizer(prompt, return_tensors="pt").to(model.device)
            output = model.generate(**inputs, max_new_tokens=96, do_sample=False,
                                    pad_token_id=tokenizer.eos_token_id)
            text = tokenizer.decode(output[0][inputs["input_ids"].shape[1]:], skip_special_tokens=True)
            try:
                hits += same(validate(json.loads(text)), row["target"], args=True)
            except (ValueError, json.JSONDecodeError):
                invalid += 1
    model.train()
    return hits / max(1, len(rows)), invalid / max(1, len(rows))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base", default=DEFAULT_BASE, help="id de Hugging Face o carpeta local del modelo base")
    parser.add_argument("--datos", type=Path, default=ROOT / ".artifacts" / "interaction")
    parser.add_argument("--salida", type=Path, default=ROOT / ".models" / "thorn-interaccion-lora")
    parser.add_argument("--epocas", type=int, default=3)
    parser.add_argument("--lote", type=int, default=8)
    parser.add_argument("--acumular", type=int, default=2, help="lotes por paso de optimización")
    parser.add_argument("--lr", type=float, default=2e-4)
    parser.add_argument("--rango", type=int, default=16, help="rango r de LoRA")
    parser.add_argument("--dropout", type=float, default=0.05, help="dropout de LoRA: más alto memoriza menos")
    parser.add_argument("--max-tokens", type=int, default=256)
    parser.add_argument("--muestra-dev", type=int, default=120, help="frases de dev generadas por época")
    parser.add_argument("--semilla", type=int, default=2026)
    parser.add_argument("--fusionar", action="store_true", help="fusiona el mejor adaptador en un modelo completo")
    args = parser.parse_args()

    try:
        import torch
        from peft import LoraConfig, PeftModel, get_peft_model
        from transformers import AutoModelForCausalLM, AutoTokenizer
    except ImportError:
        print("Falta el entorno de entrenamiento: ver docs/MODELO_DE_INTERACCION.md (Fase 2).", file=sys.stderr)
        return 2
    if not torch.cuda.is_available():
        print("No hay GPU CUDA visible para PyTorch; entrenar en CPU tardaría horas.", file=sys.stderr)
        return 2

    random.seed(args.semilla)
    torch.manual_seed(args.semilla)
    dtype = torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
    tokenizer = AutoTokenizer.from_pretrained(args.base)

    if args.fusionar:
        base = AutoModelForCausalLM.from_pretrained(args.base, torch_dtype=dtype)
        merged = PeftModel.from_pretrained(base, args.salida).merge_and_unload()
        target = args.salida.with_name(args.salida.name + "-fusionado")
        merged.save_pretrained(target, safe_serialization=True)
        tokenizer.save_pretrained(target)
        # Ollama 0.40 en Windows no ejecuta safetensors importados (solo con MLX): el
        # siguiente paso es tools/convert_interaction_gguf.py.
        print(f"Modelo fusionado en {target}. Siguiente: tools\\convert_interaction_gguf.py {target}")
        return 0

    train_rows = load(args.datos / "train.jsonl")
    dev_rows = load(args.datos / "dev.jsonl")
    encoded = [item for item in (encode(tokenizer, row, args.max_tokens) for row in train_rows) if item]
    print(f"Ejemplos de entrenamiento: {len(encoded)} de {len(train_rows)} (el resto supera {args.max_tokens} tokens)")

    model = AutoModelForCausalLM.from_pretrained(args.base, torch_dtype=dtype).to("cuda")
    model.gradient_checkpointing_enable()
    model.enable_input_require_grads()
    model = get_peft_model(model, LoraConfig(r=args.rango, lora_alpha=args.rango * 2, lora_dropout=args.dropout,
                                             target_modules=LORA_TARGETS, task_type="CAUSAL_LM"))
    model.print_trainable_parameters()

    optimizer = torch.optim.AdamW((p for p in model.parameters() if p.requires_grad), lr=args.lr, weight_decay=0.0)
    steps = math.ceil(len(encoded) / args.lote / args.acumular) * args.epocas
    warmup = max(1, steps // 20)
    scheduler = torch.optim.lr_scheduler.LambdaLR(
        optimizer, lambda step: min(1.0, (step + 1) / warmup) * max(0.0, (steps - step) / max(1, steps - warmup)))
    rng = random.Random(args.semilla)
    dev_sample = random.Random(args.semilla).sample(dev_rows, min(args.muestra_dev, len(dev_rows)))
    pad = tokenizer.pad_token_id if tokenizer.pad_token_id is not None else tokenizer.eos_token_id
    best, history = -1.0, []
    model.train()
    for epoch in range(1, args.epocas + 1):
        started, total_loss, seen = time.perf_counter(), 0.0, 0
        for index, (ids, labels, mask) in enumerate(batches(encoded, args.lote, pad, rng), 1):
            with torch.autocast("cuda", dtype=dtype):
                loss = model(input_ids=torch.tensor(ids, device="cuda"), labels=torch.tensor(labels, device="cuda"),
                             attention_mask=torch.tensor(mask, device="cuda")).loss
            (loss / args.acumular).backward()
            total_loss, seen = total_loss + loss.item(), seen + 1
            if index % args.acumular == 0:
                torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
                optimizer.step(); scheduler.step(); optimizer.zero_grad(set_to_none=True)
        accuracy, invalid = exact_match(model, tokenizer, dev_sample, torch)
        row = {"epoca": epoch, "perdida": round(total_loss / max(1, seen), 4), "dev_exacta": round(accuracy, 4),
               "dev_invalidas": round(invalid, 4), "segundos": round(time.perf_counter() - started, 1),
               "vram_max_mb": round(torch.cuda.max_memory_allocated() / 2**20)}
        history.append(row)
        print(json.dumps(row, ensure_ascii=False))
        if accuracy > best:
            best = accuracy
            model.save_pretrained(args.salida)
            tokenizer.save_pretrained(args.salida)
    (args.salida / "entrenamiento.json").write_text(json.dumps(
        {"base": args.base, "parametros": {k: str(v) for k, v in vars(args).items()}, "historial": history},
        ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Mejor exactitud en dev: {best:.1%}. Adaptador en {args.salida}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
