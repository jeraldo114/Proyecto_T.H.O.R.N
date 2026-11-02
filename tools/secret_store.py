"""Editar secretos sellados desde una terminal privada; nunca mostrarlos."""
from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.secure_storage import SECRET_NAMES, Vault, load_vault, save_vault
from thorn_web.credentials import file_lock


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--list', action='store_true')
    group.add_argument('--set', choices=sorted(SECRET_NAMES))
    parser.add_argument('--data-dir', type=Path, default=ROOT)
    args = parser.parse_args(); root = args.data_dir.resolve()
    try:
        if args.list:
            vault = load_vault(root)
            for name in sorted(SECRET_NAMES): print(name + ': ' + ('configurado' if vault.secrets.get(name) else 'vacio'))
            return 0
        if not sys.stdin.isatty():
            print('Abre una terminal privada para introducir el secreto sin eco.'); return 1
        secret = getpass.getpass('Nuevo valor (oculto): ')
        if len(secret) > 16384: raise ValueError('secret_too_large')
        if args.set == 'DJANGO_SECRET_KEY' and len(secret) < 32: raise ValueError('secret_too_short')
        with file_lock(root / 'data/security/maintenance.lock'):
            vault = load_vault(root)
            save_vault(Vault(vault.cipher, {**vault.secrets, args.set: secret}), root, replace=True)
        print('Secreto protegido actualizado. Reinicia T.H.O.R.N para aplicarlo.'); return 0
    except Exception as exc:
        print('No se modifico el almacen:', type(exc).__name__); return 1


if __name__ == '__main__':
    raise SystemExit(main())
