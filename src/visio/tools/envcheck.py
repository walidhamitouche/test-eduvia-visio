"""Vérifie qu'aucun secret n'est en clair dans le dépôt (besoin §7).

Usage : `uv run task env-check`. Code de sortie 1 si un problème est trouvé.

Contrôles :
- tout fichier `.env*` suivi (ou prêt à être ajouté) par Git, hors `.env.example`,
  ne contient que des valeurs `encrypted:...` (et les clés publiques dotenvx) ;
- aucune clé privée dotenvx dans un fichier suivi ;
- `.env.keys` n'est pas suivi et est bien ignoré par Git.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

EXAMPLE_FILE = ".env.example"
KEYS_FILE = ".env.keys"


def check_env_file_text(filename: str, text: str) -> list[str]:
    """Retourne la liste des problèmes d'un fichier .env (sans jamais citer les valeurs)."""
    problems: list[str] = []
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip().removeprefix("export ").strip()
        value = value.strip().strip("'\"")
        if key.startswith("DOTENV_PRIVATE_KEY"):
            problems.append(f"{filename}:{number} clé privée dotenvx présente ({key})")
        elif key.startswith("DOTENV_PUBLIC_KEY"):
            continue
        elif value and not value.startswith("encrypted:"):
            problems.append(f"{filename}:{number} valeur en clair pour {key}")
    return problems


def _git_lines(*args: str) -> list[str]:
    result = subprocess.run(
        ["git", *args], capture_output=True, text=True, check=True, encoding="utf-8"
    )
    return [line for line in result.stdout.splitlines() if line]


def is_keys_file(path: str) -> bool:
    """`.env.keys` et toute copie (`.env.keys.bak`, `.env.keys.old`...) : clés privées."""
    return Path(path).name.startswith(KEYS_FILE)


def _is_env_file(path: str) -> bool:
    name = Path(path).name
    return name.startswith(".env") and name != EXAMPLE_FILE


def run_checks(root: Path) -> list[str]:
    problems: list[str] = []

    tracked = _git_lines("ls-files")
    untracked = _git_lines("ls-files", "--others", "--exclude-standard")

    for path in sorted({*tracked, *untracked}):
        if is_keys_file(path):
            problems.append(f"{path} contient des clés privées et n'est pas ignoré par Git")
    ignored = subprocess.run(
        ["git", "check-ignore", "-q", KEYS_FILE], capture_output=True, check=False
    )
    if ignored.returncode != 0:
        problems.append(f"{KEYS_FILE} n'est pas ignoré par Git (ajoute-le à .gitignore)")

    for path in sorted({*tracked, *untracked}):
        if not _is_env_file(path) or is_keys_file(path):
            continue
        file_path = root / path
        if file_path.is_file():
            problems.extend(
                check_env_file_text(path, file_path.read_text(encoding="utf-8", errors="replace"))
            )
    return problems


def main() -> int:
    problems = run_checks(Path.cwd())
    if problems:
        print("Secrets en clair détectés :")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print("OK : aucun secret en clair dans le dépôt.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
