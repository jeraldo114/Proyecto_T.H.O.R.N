"""Comandos de Django para inspeccion y comprobaciones del proyecto."""

from __future__ import annotations

import os
import sys

from thorn_web.config import prepare_environment


def main() -> None:
    """Usa check y otros comandos; el servidor ASGI se inicia con serve.py."""
    if len(sys.argv) > 1 and sys.argv[1] == "runserver":
        raise SystemExit("Usa python serve.py: el runtime necesita lifespan ASGI.")
    prepare_environment()
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "thorn_web.settings")
    from django.core.management import execute_from_command_line

    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
