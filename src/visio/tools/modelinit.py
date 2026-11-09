"""Télécharge UNE FOIS le modèle Whisper dans le volume `models` (seule sortie réseau permise).

Le worker de transcription reste hors ligne (`local_files_only=True`) : ce modèle doit donc
être présent avant la première réunion. Usage en production :
    docker compose run --rm transcriber model-init
"""

from __future__ import annotations

import sys

from visio.config import get_settings


def main() -> int:
    from faster_whisper.utils import download_model  # extra `transcription`

    settings = get_settings()
    name = settings.transcription_model
    print(f"Téléchargement du modèle {name} dans {settings.transcription_model_dir} ...")
    path = download_model(name, cache_dir=settings.transcription_model_dir)
    print(f"OK : {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
