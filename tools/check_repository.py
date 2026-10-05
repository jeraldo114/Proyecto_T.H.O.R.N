"""Revisa blobs de Git sin mostrar el contenido de credenciales detectadas."""

from __future__ import annotations

import argparse
import base64
import io
import re
import subprocess
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path, PurePosixPath
from urllib.parse import quote

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
PRIVATE_DIRECTORIES = {
    ".venv", "venv", ".tools-venv", "__pycache__", "logs", "data", "backups",
    ".idea", ".vscode",
}
PRIVATE_FILE_PATTERNS = (
    "*.env", "*.log", "*.pyc", "*.pyo", "*.pem", "*.key", "*.p12", "*.pfx",
    "*.jks", "*.keystore", "*.kdbx", "*.sqlite", "*.sqlite3", "*.db", "*.dump",
    "*.sql.gz", "dump*.sql", "backup*.sql", "*.bak", "*.zip", "*.7z", "*.rar",
)
SECRET_KEY_NAME = re.compile(r"(?:PASSWORD|TOKEN|SECRET|API_KEY|PRIVATE_KEY)", re.I)
SECRET_PATTERNS = (
    re.compile(rb"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(rb"github_pat_[A-Za-z0-9_]{40,}"),
    re.compile(rb"sk-(?:proj-)?[A-Za-z0-9_-]{20,}"),
    re.compile(rb"AKIA[0-9A-Z]{16}"),
    re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH |DSA |ENCRYPTED )?PRIVATE KEY-----"),
)


@dataclass(frozen=True, slots=True)
class GitFile:
    """El hash identifica el contenido exacto que Git enviaria al remoto."""

    mode: str
    oid: str
    path: str


def git(*args: str) -> bytes:
    """Ejecuta Git en esta carpeta; nunca consulta archivos del perfil padre."""
    return subprocess.run(
        ["git", "-C", str(ROOT), *args], check=True, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ).stdout


def staged_files() -> list[GitFile]:
    """Lee el indice: una version no staged de un archivo no lo reemplaza."""
    files: list[GitFile] = []
    for record in git("ls-files", "--stage", "-z").split(b"\0"):
        if not record:
            continue
        metadata, path = record.split(b"\t", 1)
        mode, oid, stage = metadata.decode("ascii").split()
        if stage != "0":
            raise ValueError("Hay conflictos sin resolver en el indice.")
        files.append(GitFile(mode, oid, path.decode("utf-8")))
    return files


def history_files() -> list[GitFile]:
    """Revisa todas las ramas locales/remotas y deduplica ruta y contenido."""
    files: dict[tuple[str, str], GitFile] = {}
    for commit in git("rev-list", "--all").decode("ascii").splitlines():
        for record in git("ls-tree", "-r", "-z", commit).split(b"\0"):
            if not record:
                continue
            metadata, path_bytes = record.split(b"\t", 1)
            mode, _, oid = metadata.decode("ascii").split()
            path = path_bytes.decode("utf-8")
            files[(path, oid)] = GitFile(mode, oid, path)
    return list(files.values())


def private_path(path: str) -> bool:
    """Detecta rutas privadas aunque alguien haya usado git add -f."""
    parts = PurePosixPath(path.lower()).parts
    if any(part in PRIVATE_DIRECTORIES for part in parts):
        return True
    name = parts[-1]
    if (name == ".env" or name.startswith(".env.")) and name != ".env.example":
        return True
    return any(fnmatch(name, pattern) for pattern in PRIVATE_FILE_PATTERNS)


def local_secret_variants() -> list[bytes]:
    """Obtiene valores privados solo para compararlos; no los imprime."""
    env_path = ROOT / ".env"
    if not env_path.exists():
        return []
    variants: set[bytes] = set()
    for key, value in dotenv_values(env_path, interpolate=False).items():
        if not value or not SECRET_KEY_NAME.search(key):
            continue
        if len(value) < 8:
            raise ValueError(f"La clave local {key} es demasiado corta para el control.")
        encoded = value.encode("utf-8")
        variants.update((encoded, base64.b64encode(encoded), quote(value, safe="").encode()))
    return list(variants)


def content_issues(path: str, content: bytes, secrets: list[bytes]) -> list[str]:
    """Devuelve motivos, nunca valores, lineas de contenido ni fragmentos."""
    issues: list[str] = []
    if any(value in content for value in secrets):
        issues.append("contiene una credencial local o una codificacion de ella")
    if any(pattern.search(content) for pattern in SECRET_PATTERNS):
        issues.append("contiene un formato de token o clave privada")
    if PurePosixPath(path).name == ".env.example":
        template = dotenv_values(stream=io.StringIO(content.decode("utf-8")), interpolate=False)
        if any(value for key, value in template.items() if SECRET_KEY_NAME.search(key)):
            issues.append("la plantilla contiene valores privados no vacios")
    return issues


def audit(files: list[GitFile], secrets: list[bytes]) -> list[str]:
    """Comprueba nombres, tipos y contenido de los blobs seleccionados."""
    findings: list[str] = []
    for entry in files:
        if private_path(entry.path):
            findings.append(f"{entry.path}: archivo privado o generado")
        if entry.mode not in {"100644", "100755"}:
            findings.append(f"{entry.path}: enlace simbolico o submodulo no permitido")
            continue
        content = git("cat-file", "blob", entry.oid)
        for issue in content_issues(entry.path, content, secrets):
            findings.append(f"{entry.path}: {issue}")
    return findings


def main() -> int:
    """Falla de forma cerrada si Git, el indice o el control no son verificables."""
    parser = argparse.ArgumentParser(description="Control de publicacion de T.H.O.R.N")
    parser.add_argument("--history", action="store_true", help="Audita commits, no el indice.")
    args = parser.parse_args()
    try:
        if Path(git("rev-parse", "--show-toplevel").decode().strip()).resolve() != ROOT:
            raise ValueError("El repositorio debe tener su raiz en Proyecto_Thorn.")
        files = history_files() if args.history else staged_files()
        if not files:
            raise ValueError("No hay archivos de Git para revisar.")
        findings = audit(files, local_secret_variants())
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        # No se muestra stderr de Git: una URL mal configurada podria llevar un token.
        print(f"No se pudo verificar el repositorio: {type(error).__name__}.")
        return 1
    if findings:
        print("Publicacion bloqueada por el control local:")
        for finding in findings:
            print(f"- {finding}")
        return 1
    print(f"Control correcto: {len(files)} archivos revisados; no se detectaron secretos.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
