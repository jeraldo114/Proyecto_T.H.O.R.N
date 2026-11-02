"""Instala un modelo Piper publico, con revision y SHA-256 fijados."""

from __future__ import annotations

import asyncio
import hashlib
import json
import tempfile
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
NAME = "piper-es_MX-ald-medium"
REVISION = "b4e358ffcb232591bf73a540e70f7de528e4174a"
BASE = f"https://huggingface.co/rhasspy/piper-voices/resolve/{REVISION}/es/es_MX/ald/medium/"
FILES = {
    "es_MX-ald-medium.onnx": (63201294, "019b3803293c93e34a206dd2e53a3889209a514e786fd7144f7b70196c579b63"),
    "es_MX-ald-medium.onnx.json": (4878, "5a71498158e04afc8099bfd019c7e87c68eb9d042505a2b1a87e5c1ac2b1a61d"),
    "MODEL_CARD": (320, "1f0694ceb3f1e78c1e2ee0e2d5da40f19f965bc638e1b4fb4fca9bdca420d713"),
}


def install() -> None:
    """No envia datos ni sustituye modelos existentes; los pesos no entran en Git."""
    base = ROOT / ".models"
    if base.is_symlink() or not base.resolve().is_relative_to(ROOT.resolve()):
        raise ValueError("invalid_models_directory")
    base.mkdir(exist_ok=True)
    target = base / NAME
    if target.exists():
        if target.is_symlink() or not target.is_dir():
            raise ValueError("invalid_model_destination")
        for name, (size, checksum) in FILES.items():
            path = target / name
            if path.is_symlink() or not path.is_file() or path.stat().st_size != size:
                raise ValueError("existing_model_does_not_match")
            with path.open("rb") as stream:
                if hashlib.file_digest(stream, "sha256").hexdigest() != checksum:
                    raise ValueError("existing_model_does_not_match")
        print("Modelo local verificado; no se descarga de nuevo.")
        return
    print("Descargando voz es-MX local (63 MB); no se envian textos ni audio.", flush=True)
    with tempfile.TemporaryDirectory(prefix="voice-install-", dir=base) as directory:
        staged = Path(directory)
        with httpx.Client(trust_env=False, follow_redirects=True, timeout=60) as client:
            for name, (size, checksum) in FILES.items():
                digest = hashlib.sha256(); count = 0
                with client.stream("GET", BASE + name) as response:
                    response.raise_for_status()
                    with (staged / name).open("xb") as output:
                        for chunk in response.iter_bytes(65536):
                            count += len(chunk)
                            if count > size:
                                raise ValueError("voice_download_too_large")
                            digest.update(chunk); output.write(chunk)
                if count != size or digest.hexdigest() != checksum:
                    raise ValueError("voice_checksum_mismatch")
        config = json.loads((staged / "es_MX-ald-medium.onnx.json").read_text(encoding="utf-8"))
        if config.get("audio", {}).get("sample_rate") != 22050:
            raise ValueError("voice_configuration_mismatch")
        (staged / "thorn-source.json").write_text(json.dumps({"repository": "rhasspy/piper-voices", "revision": REVISION,
            "model": "es_MX-ald-medium", "source": BASE, "model_card": "MODEL_CARD", "engine_license": "GPL-3.0-or-later"}, indent=2), encoding="utf-8")
        staged.rename(target)
    print("Voz local instalada y verificada por SHA-256.")


if __name__ == "__main__":
    asyncio.run(asyncio.to_thread(install))
