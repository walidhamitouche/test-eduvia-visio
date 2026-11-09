"""Vérifie le moteur de transcription **sans aucun réseau** (étape 16c, sécurité).

    uv run task transcriber-check

Coupe toute connexion réseau du processus, charge le vrai modèle faster-whisper depuis le disque
et transcrit 2 secondes de silence. Si le modèle est absent ou si quoi que ce soit tente de sortir
sur Internet, la commande échoue. Prérequis : `uv sync --extra transcription` et le modèle copié
dans `TRANSCRIPTION_MODEL_DIR`.
"""

from __future__ import annotations

import socket
import sys
import tempfile
import wave
from collections.abc import Callable
from pathlib import Path
from typing import Any

from visio.config import ConfigurationError, get_settings
from visio.services.transcription import TranscriptionEngineError
from visio.services.whisper_engine import WhisperTranscriber


def block_network() -> None:
    def refuse(*args: Any, **kwargs: Any) -> Any:
        msg = "tentative d'accès réseau pendant la transcription"
        raise OSError(msg)

    socket.socket.connect = refuse  # type: ignore[method-assign]
    socket.getaddrinfo = refuse


def silence(path: Path, seconds: int = 2) -> None:
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(16000)
        out.writeframes(b"\x00\x00" * 16000 * seconds)


def main(out: Callable[[str], None] = print) -> int:
    try:
        settings = get_settings()
    except ConfigurationError as error:
        out(str(error))
        return 1
    block_network()
    out("réseau coupé pour ce processus")
    engine = WhisperTranscriber.from_settings(settings)
    try:
        with tempfile.TemporaryDirectory() as folder:
            audio = Path(folder) / "silence.wav"
            silence(audio)
            segments = engine.transcribe(audio, language="fr")
    except TranscriptionEngineError as error:
        out(f"ÉCHEC : {error}")
        return 1
    out(f"OK : modèle « {settings.transcription_model} » chargé et utilisé hors ligne")
    out(f"    ({len(segments)} segment(s) sur du silence, attendu : 0)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
