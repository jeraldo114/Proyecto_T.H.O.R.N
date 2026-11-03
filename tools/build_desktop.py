"""Compila codigo/recursos publicos, incluido el detector de manos; excluye datos privados."""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import ssl
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description='Compilar T.H.O.R.N standalone para Windows')
    parser.add_argument('--jobs', type=int, default=2)
    parser.add_argument('--output-dir', type=Path, default=ROOT / 'dist')
    args = parser.parse_args()
    if os.name != 'nt' or not 1 <= args.jobs <= 4:
        parser.error('Requiere Windows y entre 1 y 4 jobs.')
    destination = args.output_dir.resolve()
    destination.mkdir(exist_ok=True)
    flags = [sys.executable, '-m', 'nuitka', '--mode=standalone', '--msvc=latest', '--assume-yes-for-downloads',
        f'--jobs={args.jobs}', '--lto=no', '--python-flag=no_docstrings',
        '--python-flag=isolated', '--windows-console-mode=attach', '--output-filename=THORN.exe',
        f'--output-dir={destination}', f'--report={ROOT / ".artifacts/compilation-report.xml"}',
        '--include-package=core', '--include-package=api', '--include-package=thorn_web',
        '--include-module=serve', '--include-module=django.db.backends.dummy.base',
        '--include-package=django.conf',
        f'--user-package-configuration-file={ROOT / "tools/nuitka-package.config.yml"}',
        f'--include-data-dir={ROOT / "frontend/dist"}=frontend/dist',
        f'--include-data-files={ROOT / ".env.example"}=.env.example',
        '--nofollow-import-to=piper.train,torch,scipy,pandas,matplotlib,IPython,pytest,nuitka,pip',
        '--noinclude-data-files=**/*.py', '--noinclude-data-files=**/*.pyc',
        '--noinclude-data-files=**/*.env', '--noinclude-data-files=**/data/security/**',
        str(ROOT / 'desktop.py')]
    # Algunos runtimes incluyen politica CA suplementaria en ssl.py; conservarla.
    ca_policy = Path(ssl.__file__).with_name('codex-rejected-cas.pem')
    if ca_policy.is_file():
        flags.insert(-1, f'--include-data-files={ca_policy}={ca_policy.name}')
    # El compilador no necesita las credenciales del proceso que inicia el build.
    forbidden = {'MYSQL_PASSWORD', 'DJANGO_SECRET_KEY', 'THORN_API_TOKEN', 'ELEVENLABS_API_KEY', 'BRAVE_SEARCH_API_KEY'}
    environment = {key: value for key, value in os.environ.items() if key not in forbidden}
    result = subprocess.run(flags, cwd=ROOT, env=environment, check=False)
    return result.returncode


if __name__ == '__main__':
    raise SystemExit(main())
