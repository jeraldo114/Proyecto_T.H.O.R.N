"""Instala el modelo espanol oficial Vosk en .models; no graba ni sube audio."""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path, PurePosixPath
import stat
import tempfile
import zipfile

import httpx

ROOT = Path(__file__).resolve().parents[1]
NAME = "vosk-model-small-es-0.42"
URL = f"https://alphacephei.com/vosk/models/{NAME}.zip"
MAX_ARCHIVE = 64 * 1024 * 1024
MAX_UNPACKED = 180 * 1024 * 1024


def validate_archive(archive: zipfile.ZipFile, destination: Path) -> None:
    """Rechaza Zip Slip, enlaces y archivos fuera del unico modelo permitido."""
    entries = archive.infolist()
    if not entries or len(entries) > 500 or sum(item.file_size for item in entries) > MAX_UNPACKED:
        raise ValueError("Archivo de modelo fuera de los limites.")
    for item in entries:
        relative = PurePosixPath(item.filename)
        if (relative.is_absolute() or not relative.parts or relative.parts[0] != NAME
                or any(part in {"..", "."} or ":" in part or "\\" in part for part in relative.parts)
                or stat.S_ISLNK(item.external_attr >> 16)
                or not (destination / item.filename).resolve().is_relative_to(destination.resolve())):
            raise ValueError("Entrada ZIP no autorizada.")


def install() -> None:
    """Descarga y descomprime en un worker; no reemplaza un modelo existente."""
    base = ROOT / ".models"
    if base.is_symlink() or not base.resolve().is_relative_to(ROOT.resolve()):
        raise ValueError(".models no puede apuntar fuera del proyecto.")
    base.mkdir(exist_ok=True)
    target = base / NAME
    if target.exists():
        if not target.is_symlink() and (target / "am" / "final.mdl").is_file():
            print("Modelo existente conservado; no se descarga de nuevo.")
            return
        raise ValueError("El destino existe pero no contiene un modelo valido.")
    print("Descargando modelo espanol oficial (aprox. 39 MB); no se envia audio.", flush=True)
    with tempfile.TemporaryDirectory(prefix="speech-install-", dir=base) as directory:
        temporary = Path(directory)
        archive_path = temporary / "model.zip"
        digest, count = hashlib.sha256(), 0
        with httpx.Client(trust_env=False, follow_redirects=False, timeout=60) as client:
            with client.stream("GET", URL) as response:
                response.raise_for_status()
                if response.status_code != 200:
                    raise ValueError("El servidor oficial no entrego el archivo.")
                with archive_path.open("xb") as output:
                    for chunk in response.iter_bytes(65536):
                        count += len(chunk)
                        if count > MAX_ARCHIVE:
                            raise ValueError("Descarga demasiado grande.")
                        digest.update(chunk)
                        output.write(chunk)
        with zipfile.ZipFile(archive_path) as archive:
            validate_archive(archive, temporary)
            archive.extractall(temporary)
        staged = temporary / NAME
        for required in ("am/final.mdl", "conf/mfcc.conf", "graph/Gr.fst", "graph/HCLr.fst"):
            if not (staged / required).is_file():
                raise ValueError("Falta un componente del modelo.")
        # Huella de la descarga, no firma ni checksum publicado por el autor.
        metadata = {"source": URL, "sha256_observed": digest.hexdigest(), "license": "Apache-2.0"}
        (staged / "thorn-download.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
        staged.rename(target)
    print(f"Modelo instalado: {target}")


async def main() -> None:
    await asyncio.to_thread(install)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (httpx.HTTPError, OSError, ValueError, zipfile.BadZipFile):
        raise SystemExit("No se instalo el modelo. Revisa la red y .models; no se altero un modelo existente.") from None
