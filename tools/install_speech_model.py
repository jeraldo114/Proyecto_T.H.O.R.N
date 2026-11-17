"""Instala el modelo espanol oficial Vosk en .models; no graba ni sube audio."""

from __future__ import annotations

import argparse
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
LARGE_NAME = "vosk-model-es-0.42"
MAX_ARCHIVE = 64 * 1024 * 1024
MAX_UNPACKED = 180 * 1024 * 1024


def validate_archive(archive: zipfile.ZipFile, destination: Path, name: str = NAME, max_unpacked: int = MAX_UNPACKED) -> None:
    """Rechaza Zip Slip, enlaces y archivos fuera del unico modelo permitido."""
    entries = archive.infolist()
    if not entries or len(entries) > 500 or sum(item.file_size for item in entries) > max_unpacked:
        raise ValueError("Archivo de modelo fuera de los limites.")
    for item in entries:
        relative = PurePosixPath(item.filename)
        if (relative.is_absolute() or not relative.parts or relative.parts[0] != name
                or any(part in {"..", "."} or ":" in part or "\\" in part for part in relative.parts)
                or stat.S_ISLNK(item.external_attr >> 16)
                or not (destination / item.filename).resolve().is_relative_to(destination.resolve())):
            raise ValueError("Entrada ZIP no autorizada.")


def install(*, large: bool = False) -> None:
    """Descarga y descomprime en un worker; no reemplaza un modelo existente."""
    base = ROOT / ".models"
    if base.is_symlink() or not base.resolve().is_relative_to(ROOT.resolve()):
        raise ValueError(".models no puede apuntar fuera del proyecto.")
    base.mkdir(exist_ok=True)
    name = LARGE_NAME if large else NAME
    url = f"https://alphacephei.com/vosk/models/{name}.zip"
    max_archive = 1600 * 1024**2 if large else MAX_ARCHIVE
    max_unpacked = 4 * 1024**3 if large else MAX_UNPACKED
    target = base / name
    if target.exists():
        if not target.is_symlink() and (target / "am" / "final.mdl").is_file():
            print("Modelo existente conservado; no se descarga de nuevo.")
            return
        raise ValueError("El destino existe pero no contiene un modelo valido.")
    print(f"Descargando {name}; no se envia audio.", flush=True)
    with tempfile.TemporaryDirectory(prefix="speech-install-", dir=base) as directory:
        temporary = Path(directory)
        archive_path = temporary / "model.zip"
        digest, count = hashlib.sha256(), 0
        with httpx.Client(trust_env=False, follow_redirects=False, timeout=60) as client:
            with client.stream("GET", url) as response:
                response.raise_for_status()
                if response.status_code != 200:
                    raise ValueError("El servidor oficial no entrego el archivo.")
                with archive_path.open("xb") as output:
                    for chunk in response.iter_bytes(65536):
                        count += len(chunk)
                        if count > max_archive:
                            raise ValueError("Descarga demasiado grande.")
                        digest.update(chunk)
                        output.write(chunk)
        with zipfile.ZipFile(archive_path) as archive:
            validate_archive(archive, temporary, name, max_unpacked)
            archive.extractall(temporary)
        staged = temporary / name
        graph = ("graph/HCLG.fst",) if large else ("graph/Gr.fst", "graph/HCLr.fst")
        for required in ("am/final.mdl", "conf/mfcc.conf", *graph):
            if not (staged / required).is_file():
                raise ValueError("Falta un componente del modelo.")
        # Huella de la descarga, no firma ni checksum publicado por el autor.
        metadata = {"source": url, "sha256_observed": digest.hexdigest(), "license": "Apache-2.0"}
        (staged / "thorn-download.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
        staged.rename(target)
    print(f"Modelo instalado: {target}")


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--large", action="store_true", help="Instala el modelo de 1,4 GB para comparación.")
    await asyncio.to_thread(install, large=parser.parse_args().large)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (httpx.HTTPError, OSError, ValueError, zipfile.BadZipFile):
        raise SystemExit("No se instalo el modelo. Revisa la red y .models; no se altero un modelo existente.") from None
