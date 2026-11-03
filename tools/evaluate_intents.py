"""Línea base reproducible del parser actual; clasifica ejemplos sin ejecutarlos."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import sys
import time
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.commands import command_intent


def percentile(values: list[float], fraction: float) -> float:
    """Percentil por rango más cercano, conservando unidades de milisegundos."""
    return sorted(values)[max(0, math.ceil(len(values) * fraction) - 1)]


def evaluate(cases: list[dict[str, Any]], *, repeats: int = 50) -> dict[str, Any]:
    """Mide únicamente el parser puro, sin MySQL, modelos, voz o control de Windows."""
    if not cases or not 1 <= repeats <= 1000:
        raise ValueError("invalid_evaluation")
    ids: set[str] = set()
    for case in cases:
        if (not isinstance(case, dict) or set(case) != {"id", "group", "text", "expected"}
            or not isinstance(case["id"], str) or case["id"] in ids
            or case["group"] not in {"supported", "gap", "negative"}
            or not isinstance(case["text"], str) or not 1 <= len(case["text"]) <= 2000
            or (case["expected"] is not None and not isinstance(case["expected"], dict))):
            raise ValueError("invalid_case")
        ids.add(case["id"])
    for case in cases:
        command_intent(case["text"])
    timings, details = [], []
    groups: dict[str, Counter] = {}
    tp = fp = fn = 0
    for case in cases:
        prediction = command_intent(case["text"])
        actual = {k: v for k, v in prediction.items() if k != "reply"} if prediction else None
        for _ in range(repeats):
            started = time.perf_counter_ns()
            command_intent(case["text"])
            timings.append((time.perf_counter_ns() - started) / 1_000_000)
        correct = actual == case["expected"]
        group = groups.setdefault(case["group"], Counter())
        group.update(total=1, correct=int(correct))
        if actual is not None:
            tp += int(correct)
            fp += int(not correct)
        fn += int(case["expected"] is not None and not correct)
        # NOTA DEL OPERADOR: un informe de fallos necesita IDs y etiquetas;
        # no necesita copiar conversaciones privadas en otro archivo.
        details.append({"id": case["id"], "group": case["group"], "correct": correct,
                        "expected": case["expected"], "actual": actual})
    return {"schema": 1, "scope": "core.commands.command_intent_only",
            "cases": len(cases), "synthetic_smoke_corpus": True,
            "accuracy": sum(r["correct"] for r in details) / len(cases),
            "command_precision": tp / (tp + fp) if tp + fp else None,
            "command_recall": tp / (tp + fn) if tp + fn else None,
            "negative_false_positives": sum(r["actual"] is not None for r in details if r["expected"] is None),
            "groups": {key: dict(value) for key, value in groups.items()},
            "parser_ms": {"p50": percentile(timings, .5), "p95": percentile(timings, .95),
                          "samples": len(timings), "warmup": True},
            "details": details}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=ROOT / "tests/fixtures/intent_cases.json")
    parser.add_argument("--repeats", type=int, default=50)
    parser.add_argument("--strict", action="store_true", help="Falla si cualquier etiqueta deseada no coincide.")
    args = parser.parse_args()
    try:
        with args.cases.open("rb") as stream:
            raw = stream.read(512 * 1024 + 1)
        if len(raw) > 512 * 1024:
            raise ValueError("corpus_too_large")
        report = evaluate(json.loads(raw.decode("utf-8")), repeats=args.repeats)
        report.update(corpus_sha256=hashlib.sha256(raw).hexdigest(),
                      parser_sha256=hashlib.sha256((ROOT / "core/commands.py").read_bytes()).hexdigest(),
                      python=sys.version.split()[0])
    except (OSError, ValueError, TypeError, KeyError, RecursionError):
        print("No se pudo evaluar: revisa el formato del corpus y los límites.", file=sys.stderr)
        return 2
    output = ROOT / ".artifacts/intent-baseline.json"
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Coincidencias exactas: {round(report['accuracy'] * report['cases'])}/{report['cases']}")
    print(f"Falsas órdenes en negativos: {report['negative_false_positives']}")
    print(f"Parser caliente p95: {report['parser_ms']['p95']:.4f} ms (no mide la conversación completa)")
    print("Informe local: .artifacts/intent-baseline.json")
    return 1 if args.strict and report["accuracy"] < 1 else 0


if __name__ == "__main__":
    raise SystemExit(main())
