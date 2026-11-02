"""Prueba el EXE desde carpeta vacia, sin Python del entorno ni claves heredadas."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import wave
from pathlib import Path
from uuid import uuid4

import httpx
import psutil

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def sample_pdf() -> bytes:
    """Fixture propio: texto real que debe atravesar el lector del ejecutable."""
    from pypdf import PdfWriter
    from pypdf.generic import NameObject, DictionaryObject, DecodedStreamObject
    writer = PdfWriter(); page = writer.add_blank_page(width=300, height=300)
    page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({
        NameObject('/F1'): DictionaryObject({NameObject('/Type'): NameObject('/Font'),
            NameObject('/Subtype'): NameObject('/Type1'), NameObject('/BaseFont'): NameObject('/Helvetica')})})})
    content = DecodedStreamObject(); content.set_data(b'BT /F1 12 Tf 20 200 Td (Bastion 9: prueba documental CEDRO-9.) Tj ET')
    page[NameObject('/Contents')] = writer._add_object(content)
    output = io.BytesIO(); writer.write(output); return output.getvalue()


def resample_pcm(audio: bytes) -> bytes:
    import numpy as np
    with wave.open(io.BytesIO(audio)) as wav:
        if wav.getnchannels() != 1 or wav.getsampwidth() != 2: raise ValueError('unexpected_wav')
        rate = wav.getframerate(); values = np.frombuffer(wav.readframes(wav.getnframes()), dtype='<i2')
    positions = np.arange(min(240000, int(len(values) * 16000 / rate)), dtype=np.float64) * rate / 16000
    return np.clip(np.interp(positions, np.arange(len(values)), values), -32768, 32767).astype('<i2').tobytes()


async def check_api(url: str) -> dict[str, object]:
    from thorn_web.config import prepare_environment
    await asyncio.to_thread(prepare_environment)
    async with httpx.AsyncClient(base_url=url, trust_env=False, follow_redirects=False, timeout=75) as client:
        issued = await client.post('/api/v1/auth/session/', json={}, headers={'Authorization': 'Bearer ' + os.environ['THORN_API_TOKEN']})
        issued.raise_for_status(); headers = {'Authorization': 'Bearer ' + issued.json()['access_token']}
        note_id = None
        try:
            response = await client.get('/api/v1/capabilities/', headers=headers); response.raise_for_status()
            caps = response.json()
            from core.version import RELEASE, MODEL_PROFILE
            if caps['identity']['version'] != RELEASE or caps['language']['model'] != MODEL_PROFILE:
                raise ValueError('compiled_version_mismatch')
            if not caps['storage']['encrypted'] or caps['voice']['state'] != 'ready' or caps['microphone']['state'] != 'ready' or not caps['language']['available']:
                raise ValueError('compiled_capabilities_incomplete')
            if caps['operator']['memory']['state'] != 'disabled' or caps['operator']['memory']['retention'] != 'until_deleted':
                raise ValueError('compiled_conversation_module_missing')
            for asset in ('ocr.js', 'pdf-viewer.js', 'spa.traineddata.gz'):
                response = await client.get('/hud/assets/' + asset); response.raise_for_status()
                expected = await asyncio.to_thread((ROOT / 'frontend/dist' / asset).read_bytes)
                if hashlib.sha256(response.content).digest() != hashlib.sha256(expected).digest():
                    raise ValueError('compiled_ocr_assets_mismatch')
            voice = await client.post('/api/v1/voice/speech/', headers=headers,
                json={'text': 'Verificación de voz local. Estoy listo para continuar.', 'cloud_consent': False})
            voice.raise_for_status()
            if voice.headers.get('content-type') != 'audio/wav' or not voice.content.startswith(b'RIFF'):
                raise ValueError('compiled_voice_failed')
            pcm = await asyncio.to_thread(resample_pcm, voice.content)
            stt = await client.post('/api/v1/microphone/transcribe/', headers={**headers,
                'Content-Type': 'application/octet-stream', 'X-Thorn-Microphone-Consent': 'local'}, content=pcm)
            stt.raise_for_status()
            if stt.json()['cloud'] is not False or not stt.json()['text'].strip():
                raise ValueError('compiled_transcription_failed')
            note = 'compiled-encryption-check-' + uuid4().hex
            saved = await client.post('/api/v1/memory/save/', headers=headers, json={'text': note})
            saved.raise_for_status(); note_id = saved.json()['id']
            notes = await client.get('/api/v1/memory/', headers=headers); notes.raise_for_status()
            if not any(item['id'] == note_id and item['text'] == note for item in notes.json()['notes']):
                raise ValueError('compiled_memory_failed')
            pdf = await client.post('/api/v1/documents/upload/', headers={**headers,
                'Content-Type': 'application/pdf', 'X-Thorn-Document-Consent': 'local'}, content=sample_pdf())
            pdf.raise_for_status()
            if not any('CEDRO-9' in page['text'] for page in pdf.json()['document']['pages']):
                raise ValueError('compiled_document_failed')
            start = time.monotonic()
            query = asyncio.create_task(client.post('/api/v1/chat/', headers=headers,
                json={'prompt': 'Mi proyecto de prueba se llama Cedro. Confirma ese nombre en una frase.'}))
            health_start = time.monotonic(); health = await client.get('/api/v1/health/'); health.raise_for_status()
            health_ms = round((time.monotonic() - health_start) * 1000)
            answer = await query; answer.raise_for_status()
            if not answer.json()['reply'].strip() or answer.json()['executed'] is not False:
                raise ValueError('compiled_model_failed')
            follow = await client.post('/api/v1/chat/', headers=headers, json={'prompt': 'Como se llama mi proyecto de prueba?'})
            follow.raise_for_status()
            if 'cedro' not in follow.json()['reply'].casefold() or follow.json()['context_messages'] != 4:
                raise ValueError('compiled_context_failed')
            return {'compiled': True, 'encrypted_storage': True, 'voice_bytes': len(voice.content),
                'stt': 'local_real_audio', 'model': caps['language']['model'], 'release': RELEASE,
                'pdf_text': True, 'context_follow_up': True,
                'local_ocr_assets': True, 'conversation_module': True,
                'chat_ms': round((time.monotonic() - start) * 1000), 'health_ms': health_ms}
        finally:
            if note_id:
                deleted = await client.post('/api/v1/memory/delete/', headers=headers, json={'id': note_id})
                deleted.raise_for_status()
            await client.post('/api/v1/auth/revoke/', headers=headers, json={})


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', type=Path, default=ROOT / 'dist/desktop.dist/THORN.exe')
    parser.add_argument('--data-dir', type=Path, default=ROOT)
    args = parser.parse_args()
    if os.name != 'nt' or not args.binary.is_file(): parser.error('Requiere el ejecutable Windows compilado.')
    root = args.data_dir.resolve(); os.environ['THORN_DATA_DIR'] = str(root)
    forbidden = {'THORN_API_TOKEN', 'DJANGO_SECRET_KEY', 'MYSQL_PASSWORD', 'ELEVENLABS_API_KEY', 'BRAVE_SEARCH_API_KEY',
                 'PYTHONHOME', 'PYTHONPATH', 'THORN_DATA_DIR'}
    environment = {key:value for key,value in os.environ.items() if key not in forbidden}
    environment['THORN_CONVERSATION_AUTO_START'] = '0'  # La prueba usa audio sintetico, no abre el microfono fisico.
    environment['THORN_CONVERSATION_MEMORY'] = '0'  # No mezclar preguntas de prueba con el cuaderno real del operador.
    log_path = ROOT / '.artifacts/compiled-services.log'
    with tempfile.TemporaryDirectory(prefix='thorn-exe-test-', ignore_cleanup_errors=True) as working, log_path.open('wb') as log:
        process = subprocess.Popen([str(args.binary.resolve()), '--background', '--data-dir', str(root)],
            cwd=working, env=environment, stdout=log, stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            target = psutil.Process(process.pid)
            deadline = time.monotonic() + 45; url = None
            with httpx.Client(trust_env=False, timeout=1) as probe:
                while time.monotonic() < deadline and process.poll() is None:
                    for connection in target.net_connections(kind='inet'):
                        if connection.status != psutil.CONN_LISTEN or connection.laddr.ip != '127.0.0.1': continue
                        candidate = f'http://127.0.0.1:{connection.laddr.port}'
                        try:
                            response = probe.get(candidate + '/api/v1/health/')
                            if response.status_code == 200 and response.json().get('name') == 'T.H.O.R.N': url = candidate; break
                        except (httpx.HTTPError, ValueError): pass
                    if url: break
                    time.sleep(.2)
            if not url: raise RuntimeError('compiled_startup_failed')
            result = asyncio.run(check_api(url))
            with args.binary.open('rb') as binary:
                result['binary_sha256'] = hashlib.file_digest(binary, 'sha256').hexdigest()
            (ROOT / '.artifacts/compiled-verification.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
            print(json.dumps(result)); return 0
        except Exception as exc:
            print('Verificacion compilada detenida:', type(exc).__name__, '. Diagnostico privado:', log_path); return 1
        finally:
            # Solo el proceso creado por esta prueba; todas las consultas ya terminaron.
            children = []
            if process.poll() is None:
                try: children = psutil.Process(process.pid).children(recursive=True)
                except psutil.NoSuchProcess: pass
                process.terminate(); process.wait(timeout=15)
            _, alive = psutil.wait_procs(children, timeout=3)
            for child in alive:
                try: child.terminate()
                except psutil.NoSuchProcess: pass
            psutil.wait_procs(alive, timeout=3)


if __name__ == '__main__':
    raise SystemExit(main())
