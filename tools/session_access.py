"""Obtiene una sesion de navegador usando la autoridad protegida, sin mostrarla."""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from thorn_web.config import prepare_environment


async def issue(port: int) -> None:
    await asyncio.to_thread(prepare_environment)
    async with httpx.AsyncClient(trust_env=False, follow_redirects=False, timeout=6) as client:
        response = await client.post(f'http://127.0.0.1:{port}/api/v1/auth/session/', json={},
            headers={'Authorization': 'Bearer ' + os.environ['THORN_API_TOKEN']})
        if response.status_code != 200:
            raise RuntimeError('session_unavailable')
        value = response.json()
    print('Sesion temporal de acceso completo. Pegala en Conectar; no la compartas.')
    print('Caduca a los 15 minutos; el HUD la renueva hasta el limite de 24 horas.')
    print(value['access_token'])  # Solo por solicitud explicita del propietario en su terminal.


def main() -> int:
    parser = argparse.ArgumentParser(description='Acceso temporal al HUD local de T.H.O.R.N')
    parser.add_argument('--port', type=int, default=8004)
    parser.add_argument('--mostrar-token', action='store_true', help='Mostrar SOLO una sesion efimera en esta terminal privada.')
    args = parser.parse_args()
    if not 1 <= args.port <= 65535: parser.error('Puerto invalido.')
    if not args.mostrar_token:
        print('El escritorio conecta automaticamente. Para un navegador, usa --mostrar-token en una terminal privada.')
        return 0
    try: asyncio.run(issue(args.port)); return 0
    except (OSError, RuntimeError, httpx.HTTPError, ValueError, KeyError):
        print('No se pudo obtener una sesion. Comprueba que T.H.O.R.N este abierto y usa el mismo usuario Windows.')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
