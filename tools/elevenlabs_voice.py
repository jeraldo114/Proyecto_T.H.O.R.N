"""Disena una voz ORIGINAL en dos pasos; no descarga ni sube muestras de actores."""

from __future__ import annotations

import argparse
import asyncio
import base64
import binascii
import json
import os
from pathlib import Path
import sys
from uuid import uuid4

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.elevenlabs_voice import API_ORIGIN, MAX_AUDIO, VoiceConfig, valid_voice_id
from core.personality import VOICE_DESCRIPTION, VOICE_PREVIEW


def provider_error(status: int, data: object) -> str:
    """Traduce solo motivos conocidos; nunca repite mensajes ni secretos remotos."""
    explanations = {
        "paid_plan_required": "Este endpoint requiere un plan de pago; no se modifica tu suscripcion.",
        "missing_permissions": "La API key no tiene los permisos necesarios para esta operacion.",
        "quota_exceeded": "El proveedor indica que la cuota esta agotada.",
        "insufficient_credits": "El proveedor indica que faltan creditos.",
        "invalid_api_key": "El proveedor rechazo la API key.",
        "feature_not_available": "Esta funcion no esta disponible para tu cuenta o plan.",
        "detected_unusual_activity": "El proveedor bloqueo la operacion por su control de actividad.",
        "voice_access_denied": "La cuenta no tiene acceso a esa voz.",
        "rate_limit_exceeded": "El proveedor limito las solicitudes.",
    }
    detail = data.get("detail", {}) if isinstance(data, dict) else {}
    code = detail.get("status") if isinstance(detail, dict) else None
    message = detail.get("message") if isinstance(detail, dict) else None
    if not isinstance(code, str) or code not in explanations:
        code = "unspecified"
    # El texto se usa solo para clasificar: jamas se imprime o persiste.
    if isinstance(message, str) and status == 403 and any(
        marker in message.lower() for marker in ("paid plan", "paid subscription", "paid tier", "starter plan")
    ):
        code = "paid_plan_required"
    explanation = explanations.get(code, "Revisa permisos, plan y creditos en tu cuenta.")
    return f"ElevenLabs HTTP {status} ({code}). {explanation}"


async def provider_json(client: httpx.AsyncClient, method: str, path: str, *,
                        key: str, body: dict[str, object] | None = None,
                        params: dict[str, object] | None = None) -> dict[str, object]:
    """Lee JSON acotado sin imprimir errores del proveedor ni reintentar cargos."""
    async with asyncio.timeout(60):
        async with client.stream(method, path, headers={"xi-api-key": key},
                                 json=body, params=params) as response:
            status = response.status_code
            limit = 9 * 1024 * 1024 if status == 200 else 16 * 1024
            raw = bytearray()
            async for chunk in response.aiter_bytes(chunk_size=4096):
                if len(raw) + len(chunk) > limit:
                    if status != 200:
                        raise RuntimeError(provider_error(status, {}))
                    raise ValueError("Respuesta ElevenLabs demasiado grande.")
                raw.extend(chunk)
    try:
        data = json.loads(raw)
    except (ValueError, RecursionError):
        if status != 200:
            raise RuntimeError(provider_error(status, {})) from None
        raise ValueError("ElevenLabs no entrego JSON valido.") from None
    if status != 200:
        raise RuntimeError(provider_error(status, data))
    if not isinstance(data, dict):
        raise ValueError("Contrato de respuesta ElevenLabs invalido.")
    return data


def write_previews(data: dict[str, object], *, root: Path = ROOT) -> Path:
    """Guarda solo vistas previas originales en un directorio privado unico."""
    previews = data.get("previews")
    if not isinstance(previews, list) or not 1 <= len(previews) <= 3:
        raise ValueError("No se recibieron de una a tres vistas previas.")
    decoded: list[tuple[str, bytes]] = []
    for preview in previews:
        if not isinstance(preview, dict) or not valid_voice_id(preview.get("generated_voice_id")):
            raise ValueError("Identificador de vista previa invalido.")
        audio = preview.get("audio_base_64")
        if not isinstance(audio, str) or len(audio) > 4 * ((MAX_AUDIO + 2) // 3):
            raise ValueError("Vista previa demasiado grande o ausente.")
        try:
            raw = base64.b64decode(audio, validate=True)
        except (binascii.Error, ValueError):
            raise ValueError("Audio base64 invalido.") from None
        if not raw or len(raw) > MAX_AUDIO or preview.get("media_type") not in {"audio/mpeg", "audio/mp3"}:
            raise ValueError("Formato de vista previa invalido.")
        decoded.append((preview["generated_voice_id"], raw))
    base = root / "data" / "voice-design"
    if not base.resolve().is_relative_to(root.resolve()) or base.is_symlink():
        raise ValueError("El directorio privado de audio no puede apuntar fuera del proyecto.")
    destination = base / uuid4().hex
    destination.mkdir(parents=True, exist_ok=False)
    metadata = []
    for index, (identity, raw) in enumerate(decoded, 1):
        name = f"preview-{index}.mp3"
        with (destination / name).open("xb") as stream:
            stream.write(raw)
        metadata.append({"file": name, "generated_voice_id": identity})
    with (destination / "previews.json").open("x", encoding="utf-8") as stream:
        json.dump({"previews": metadata, "original_voice": True}, stream, indent=2)
    return destination


async def run(args: argparse.Namespace) -> None:
    """Los unicos comandos remotos son listar, disenar y guardar voz original."""
    from thorn_web.config import prepare_environment
    await asyncio.to_thread(prepare_environment)
    key = os.getenv("ELEVENLABS_API_KEY", "")
    VoiceConfig(api_key=key)
    if not key:
        raise ValueError("Falta ELEVENLABS_API_KEY en el .env privado. Nunca la pegues en un chat.")
    if args.command == "design" and not args.accept_cloud_cost:
        raise ValueError("Disenar envia el prompt y texto a ElevenLabs y consume creditos. Usa --accept-cloud-cost.")
    if args.command == "save":
        if not args.accept_account_change:
            raise ValueError("Guardar anade una voz y ocupa un cupo en tu cuenta. Usa --accept-account-change.")
        if not valid_voice_id(args.preview):
            raise ValueError("Identificador de vista previa invalido.")
    # Construir el contexto TLS puede leer certificados; se hace fuera del loop.
    client = await asyncio.to_thread(
        httpx.AsyncClient, base_url=API_ORIGIN, trust_env=False,
        follow_redirects=False, timeout=httpx.Timeout(45, connect=5),
    )
    async with client:
        if args.command == "voices":
            data = await provider_json(client, "GET", "/v2/voices", key=key,
                                       params={"page_size": 100, "search": args.search})
            voices = data.get("voices")
            if not isinstance(voices, list):
                raise ValueError("Listado de voces invalido.")
            safe = [{"voice_id": voice.get("voice_id"), "name": str(voice.get("name", ""))[:120],
                     "labels": voice.get("labels")} for voice in voices
                    if isinstance(voice, dict) and valid_voice_id(voice.get("voice_id"))]
            print(json.dumps({"voices": safe, "has_more": bool(data.get("has_more"))}, ensure_ascii=True, indent=2))
        elif args.command == "design":
            data = await provider_json(client, "POST", "/v1/text-to-voice/design", key=key, body={
                "model_id": "eleven_multilingual_ttv_v2", "voice_description": VOICE_DESCRIPTION,
                "text": VOICE_PREVIEW, "auto_generate_text": False, "guidance_scale": 5,
            }, params={"output_format": "mp3_44100_128"})
            destination = await asyncio.to_thread(write_previews, data)
            print(f"Vistas previas ORIGINALES: {destination}")
            print("Escucha los MP3. previews.json contiene los ID para el comando save.")
        elif args.command == "save":
            data = await provider_json(client, "POST", "/v1/text-to-voice", key=key, body={
                "voice_name": "T.H.O.R.N - Bastion 8 / Latino", "voice_description": VOICE_DESCRIPTION,
                "generated_voice_id": args.preview,
                "labels": {"language": "es", "gender": "male", "description": "original tactical assistant"},
            })
            if not valid_voice_id(data.get("voice_id")):
                raise ValueError("La voz no entrego un ID valido; revisa My Voices antes de repetir.")
            print("Voz original guardada en tu cuenta. Configura en el .env privado:")
            print("ELEVENLABS_VOICE_ID=" + data["voice_id"])
            print("THORN_VOICE_ENABLED=1")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    voices = commands.add_parser("voices", help="Lista hasta 100 voces de tu cuenta, sin sintetizar")
    voices.add_argument("--search", default="", help="Filtra por nombre/descripcion; no demuestra derechos de uso")
    design = commands.add_parser("design", help="Genera tres muestras de una identidad vocal original")
    design.add_argument("--accept-cloud-cost", action="store_true")
    save = commands.add_parser("save", help="Guarda la muestra original elegida en My Voices")
    save.add_argument("--preview", required=True)
    save.add_argument("--accept-account-change", action="store_true")
    try:
        asyncio.run(run(parser.parse_args()))
    except (ValueError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except (httpx.HTTPError, TimeoutError):
        print("No se completo la solicitud remota. Revisa tu cuenta antes de repetir; puede haber consumo.", file=sys.stderr)
        return 1
    except OSError:
        print("No se pudo leer la configuracion o guardar las vistas previas privadas.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
