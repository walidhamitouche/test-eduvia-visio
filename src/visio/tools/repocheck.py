"""Contrôle du dépôt avant livraison (étape 21b) : `uv run task repo-check`.

Refuse tout ce qui ne doit jamais être versionné (clés privées, bases locales, configuration
générée avec des clés, sauvegardes) et vérifie que les fichiers `.env.*` versionnés sont
**chiffrés** par dotenvx. Utilise `git ls-files` : hors dépôt Git, le dit et sort en erreur.
"""

from __future__ import annotations

import fnmatch
import re
import subprocess
import sys
from pathlib import Path

FORBIDDEN = (
    ".env.keys",
    ".env.keys.*",
    ".env.local",
    "*.pem",
    "*.key",
    "*.db",
    "*.db-wal",
    "*.db-shm",
    "data/*",
    "*/.generated/*",
    "deploy/dev/livekit-server.exe",
    "*.keys.bak",
    "node_modules/*",
    "*/node_modules/*",
    "__pycache__/*",
    "*/__pycache__/*",
    "web/dist/*",
)
ENV_VALUE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$")
PLAIN_OK = {"DOTENV_PUBLIC_KEY", "DOTENV_PUBLIC_KEY_PRODUCTION", "DOTENV_PUBLIC_KEY_DEVELOPMENT"}


def forbidden(paths: list[str]) -> list[str]:
    """Les chemins versionnés qui correspondent à un motif interdit."""
    return [p for p in paths if any(fnmatch.fnmatch(p, pattern) for pattern in FORBIDDEN)]


def plaintext_env_values(name: str, text: str) -> list[str]:
    """Variables d'un fichier `.env.*` dont la valeur n'est pas chiffrée (`encrypted:`)."""
    bad: list[str] = []
    for line in text.splitlines():
        match = ENV_VALUE.match(line.strip())
        if match is None or line.lstrip().startswith("#"):
            continue
        key, value = match.group(1), match.group(2).strip().strip("\"'")
        if key in PLAIN_OK or key.startswith("DOTENV_PUBLIC_KEY") or value == "":
            continue
        if not value.startswith("encrypted:"):
            bad.append(f"{name} : {key} en clair")
    return bad


def tracked_files() -> list[str] | None:
    try:
        out = subprocess.run(["git", "ls-files"], capture_output=True, text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return None
    return [line for line in out.splitlines() if line]


def main() -> int:
    files = tracked_files()
    if files is None:
        print("Pas un dépôt Git (ou git absent) : `git init`, puis `git add .` avant ce contrôle.")
        return 1
    problems = [f"fichier à ne pas versionner : {p}" for p in forbidden(files)]
    for path in files:
        name = Path(path).name
        # `.env.example` est un modèle documenté (valeurs d'exemple, pas des secrets) ; `.env.test`
        # ne sert qu'aux tests.
        if name.startswith(".env.") and name not in {".env.example", ".env.test", ".env.keys"}:
            problems += plaintext_env_values(path, Path(path).read_text(encoding="utf-8"))
    for problem in problems:
        print(f"  PROBLÈME : {problem}")
    print(f"{len(files)} fichiers versionnés, {len(problems)} problème(s).")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
