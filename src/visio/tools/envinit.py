"""Crée et chiffre un fichier `.env.<environnement>` avec dotenvx.

Usage : `uv run task env-init development` (ou `test`, `production`).

- génère des secrets aléatoires (clé API, signature des liens, secret des webhooks) ;
- écrit le fichier, le chiffre aussitôt avec `dotenvx encrypt` ;
- la clé privée est créée dans `.env.keys` (jamais versionné) : à sauvegarder ailleurs.

Les valeurs propres à l'infrastructure (S3, LiveKit, domaine) sont des valeurs de
développement, ou `CHANGE_ME` en production : elles se remplacent ensuite avec
`dotenvx set NOM "valeur" -f .env.production`.
"""

from __future__ import annotations

import secrets
import shutil
import subprocess
import sys
from pathlib import Path

ENVIRONMENTS = ("development", "test", "production")


def build_values(env: str) -> dict[str, str]:
    values: dict[str, str] = {
        "APP_ENV": env,
        "API_KEY": secrets.token_urlsafe(48),
        "LINK_SIGNING_SECRET": secrets.token_urlsafe(48),
        "WEBHOOK_SECRET": secrets.token_urlsafe(48),
        "BRAND_NAME": "Eduvia Visio",
        "BRAND_PRIMARY_COLOR": "#E8505B",
        "BRAND_SECONDARY_COLOR": "#1F1F1F",
        "S3_REGION": "eu-west-3",
        "DATABASE_URL": "sqlite:///./data/visio.db",
    }
    if env == "production":
        values |= {
            "PUBLIC_BASE_URL": "https://visio.eduvia.app",
            "WEBHOOK_URL": "https://CHANGE_ME/webhooks/visio",
            "LIVEKIT_URL": "wss://visio.eduvia.app",
            "LIVEKIT_API_KEY": "CHANGE_ME",
            "LIVEKIT_API_SECRET": "CHANGE_ME",
            "S3_ENDPOINT": "https://CHANGE_ME",
            "S3_BUCKET": "CHANGE_ME",
            "S3_ACCESS_KEY": "CHANGE_ME",
            "S3_SECRET_KEY": "CHANGE_ME",
            "BRAND_LOGO_URL": "https://visio.eduvia.app/brand/logo.svg",
            "RECORDING_TEMPLATE_URL": "https://CHANGE_ME/recording-template",
        }
    else:
        values |= {
            "PUBLIC_BASE_URL": "http://localhost:8000",
            "WEBHOOK_URL": "http://localhost:9999/webhooks/visio",
            "LIVEKIT_URL": "ws://localhost:7880",
            "LIVEKIT_API_KEY": "devkey",
            "LIVEKIT_API_SECRET": secrets.token_urlsafe(48),
            "S3_ENDPOINT": "http://localhost:9000",
            "S3_BUCKET": f"visio-{env}",
            "S3_ACCESS_KEY": "visio-dev",
            "S3_SECRET_KEY": secrets.token_urlsafe(32),
            "BRAND_LOGO_URL": "http://localhost:8000/brand/logo.svg",
            "RECORDING_TEMPLATE_URL": "http://host.docker.internal:8000/recording-template",
        }
    return values


def render(values: dict[str, str]) -> str:
    """Valeurs entre guillemets : sans eux, dotenvx prend `#` (couleurs) pour un commentaire."""
    return "".join(f'{key}="{value}"\n' for key, value in values.items())


def main(argv: list[str]) -> int:
    force = "--force" in argv
    args = [a for a in argv if not a.startswith("--")]
    if len(args) != 1 or args[0] not in ENVIRONMENTS:
        print(f"Usage : env-init <{'|'.join(ENVIRONMENTS)}> [--force]")
        return 2
    env = args[0]

    dotenvx = shutil.which("dotenvx")
    if dotenvx is None:
        print("dotenvx est introuvable. Installe-le : npm install -g @dotenvx/dotenvx")
        return 1

    target = Path(f".env.{env}")
    if target.exists() and not force:
        print(f"{target} existe déjà (utilise --force pour le régénérer : nouveaux secrets !).")
        return 1

    target.write_text(render(build_values(env)), encoding="utf-8", newline="\n")
    result = subprocess.run([dotenvx, "encrypt", "-f", str(target)], check=False)
    if result.returncode != 0:
        target.unlink(missing_ok=True)
        print("Le chiffrement a échoué : le fichier en clair a été supprimé.")
        return 1

    print(
        f"{target} créé et chiffré. Sauvegarde la clé DOTENV_PRIVATE_KEY_{env.upper()} (.env.keys)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
