"""Avisos de dependencias, firma y ZIP publico de una distribucion ya verificada."""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import re
import shutil
import sys
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.sign_release import initialize_signer, sign, verify
from tools.check_repository import local_secret_variants
from core.secure_storage import encode64
from core.version import RELEASE, VERSION


def assert_verified(directory: Path, record: Path | None = None) -> None:
    """No etiqueta como nueva una compilacion vieja o distinta de la probada."""
    path = record if record is not None else ROOT / '.artifacts/compiled-verification.json'
    value = json.loads(path.read_text(encoding='utf-8'))
    with (directory / 'THORN.exe').open('rb') as binary:
        digest = hashlib.file_digest(binary, 'sha256').hexdigest()
    if (not isinstance(value, dict) or value.get('release') != RELEASE or
            value.get('binary_sha256') != digest or not all(value.get(key) is True
            for key in ('compiled', 'encrypted_storage', 'pdf_text', 'context_follow_up'))):
        raise ValueError('verify_current_compilation_first')


def notices(directory: Path) -> None:
    components = []
    report = ET.parse(ROOT / '.artifacts/compilation-report.xml')
    names = {node.attrib['name'] for node in report.iter('distribution')} | {'nuitka'}
    for name in sorted(names, key=str.lower):
        distribution = importlib.metadata.distribution(name)
        slug = re.sub('[^a-z0-9_.-]', '_', name.lower())
        destination = directory / 'licenses' / slug; destination.mkdir(parents=True, exist_ok=True)
        copied = []
        for item in distribution.files or ():
            if not any(token in str(item).lower() for token in ('license', 'copying', 'notice')): continue
            if item.suffix.lower() in {'.py', '.pyc', '.dll', '.pyd', '.so'}: continue
            source = distribution.locate_file(item)
            if not source.is_file() or source.stat().st_size > 2 * 1024 * 1024: continue
            target = destination / item.name
            if target.exists() and target.read_bytes() != source.read_bytes():
                target = destination / (hashlib.sha256(str(item).encode()).hexdigest()[:8] + '-' + item.name)
            shutil.copyfile(source, target); copied.append(target.relative_to(directory).as_posix())
        components.append({'name': name, 'version': distribution.version,
            'license': distribution.metadata.get('License-Expression') or distribution.metadata.get('License'), 'notices': copied})
    python_license = Path(sys.base_prefix) / 'LICENSE.txt'
    target = directory / 'licenses/Python'; target.mkdir(exist_ok=True)
    shutil.copyfile(python_license, target / 'LICENSE.txt')
    node_root = ROOT / 'frontend/node_modules'
    for name in ('@fontsource/chakra-petch', 'lucide', 'three', '@ricky0123/vad-web', 'onnxruntime-web', '@mediapipe/tasks-vision', 'pdfjs-dist', 'leaflet'):
        metadata = json.loads((node_root / name / 'package.json').read_text(encoding='utf-8'))
        target = directory / 'licenses' / re.sub('[^a-z0-9_.-]', '_', name); target.mkdir(exist_ok=True)
        for source in (node_root / name).glob('*'):
            if source.is_file() and any(word in source.name.lower() for word in ('license', 'notice')):
                shutil.copyfile(source, target / source.name)
        components.append({'name': name, 'version': metadata['version'], 'license': metadata.get('license')})
    # Los decodificadores del visor llevan avisos propios, ademas del paquete JS.
    pdf_licenses = directory / 'licenses/pdfjs-decoders'; pdf_licenses.mkdir(exist_ok=True)
    for folder in ('wasm', 'standard_fonts', 'cmaps'):
        for source in (node_root / 'pdfjs-dist' / folder).glob('LICENSE*'):
            shutil.copyfile(source, pdf_licenses / (folder + '-' + source.name))
    # Algunos paquetes npm omiten sus avisos del tarball. Recuperar el texto de
    # sus repositorios primarios y conservar URL y hash del aviso recibido.
    sources = {
        'vad-web': 'https://raw.githubusercontent.com/ricky0123/vad/master/LICENSE',
        'silero-vad': 'https://raw.githubusercontent.com/snakers4/silero-vad/master/LICENSE',
        'onnxruntime-web': 'https://raw.githubusercontent.com/microsoft/onnxruntime/v1.22.0/LICENSE',
        'onnxruntime-third-party': 'https://raw.githubusercontent.com/microsoft/onnxruntime/v1.22.0/ThirdPartyNotices.txt',
        'mediapipe': 'https://raw.githubusercontent.com/google-ai-edge/mediapipe/master/LICENSE',
    }
    cache = ROOT / '.artifacts/license-cache'; cache.mkdir(exist_ok=True)
    with httpx.Client(trust_env=False, follow_redirects=False, timeout=20) as client:
        for name, url in sources.items():
            cached = cache / (name + '.txt')
            if not cached.exists():
                response = client.get(url); response.raise_for_status()
                if len(response.content) > 2 * 1024 * 1024: raise ValueError('notice_too_large')
                cached.write_bytes(response.content)
            target = directory / 'licenses' / name; target.mkdir(exist_ok=True)
            shutil.copyfile(cached, target / 'LICENSE.txt')
            components.append({'name': name, 'notice_source': url, 'notice_sha256': hashlib.sha256(cached.read_bytes()).hexdigest()})
    hand_model = directory / 'frontend/dist/hand_landmarker.task'
    if hand_model.is_file():
        components.append({'name': 'MediaPipe Hand Landmarker (float16)', 'license': 'Apache-2.0',
            'sha256': hashlib.sha256(hand_model.read_bytes()).hexdigest(),
            'source': 'https://developers.google.com/edge/mediapipe/solutions/vision/hand_landmarker/index',
            'model_card': 'https://storage.googleapis.com/mediapipe-assets/Model%20Card%20Hand%20Tracking%20%28Lite_Full%29%20with%20Fairness%20Oct%202021.pdf'})
    (directory / 'THIRD_PARTY_NOTICES.json').write_text(json.dumps(components, indent=2), encoding='utf-8')
    if (ROOT / 'LICENSE').is_file(): shutil.copyfile(ROOT / 'LICENSE', directory / 'LICENSE')


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, default=ROOT / 'dist/desktop.dist')
    args = parser.parse_args(); directory = args.directory.resolve()
    try:
        if not (directory / 'THORN.exe').is_file(): raise ValueError('compiled_binary_required')
        assert_verified(directory)
        notices(directory)
        (directory / 'README-DISTRIBUCION.txt').write_text(
            f'T.H.O.R.N - {RELEASE} ({VERSION}) / Windows x64\n\n'
            'Ejecutar THORN.exe --data-dir C:\\Ruta\\THORN_datos\n'
            'Requiere un perfil cifrado preparado, MySQL, Ollama y modelos locales.\n'
            'Sin configuracion privada ni claves. Incluye el detector publico de manos si se instalo antes de compilar.\n'
            'Los modelos de lenguaje, voz y dictado permanecen en el perfil local. No requiere Python instalado.\n'
            'La firma Ed25519 del manifiesto se verifica con una clave publica confiable externa al ZIP.\n'
            'No es Authenticode ni una garantia de que el codigo sea imposible de inspeccionar.\n'
            'Conservar los avisos de componentes y revisar sus condiciones antes de redistribuir.\n'
            'Codigo e instrucciones: https://github.com/jeraldo114/Proyecto_T.H.O.R.N\n', encoding='utf-8')
        key = initialize_signer(ROOT)
        private = tuple(local_secret_variants()) + (key.private_bytes_raw(), encode64(key.private_bytes_raw()).encode())
        value = sign(directory, key, private)
        archive = ROOT / '.artifacts' / f'THORN-{RELEASE.replace(" ", "")}-Windows-x64.zip'
        with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as output:
            for path in sorted(directory.rglob('*')):
                if path.is_file(): output.write(path, 'THORN/' + path.relative_to(directory).as_posix())
        verify(directory, key.public_key().public_bytes_raw())
        print('Paquete firmado:', len(value['files']), 'archivos. ZIP:', archive)
        with archive.open('rb') as stream:
            print('SHA-256 ZIP:', hashlib.file_digest(stream, 'sha256').hexdigest())
        return 0
    except Exception as exc:
        print('Paquete detenido:', type(exc).__name__); return 1


if __name__ == '__main__':
    raise SystemExit(main())
