"""Transcrit un fichier audio ou vidéo avec le moteur local, pour comparer modèles et réglages.

    uv run task transcribe-file audio.wav [--model large-v3-turbo] [--vocab "Airflow, Python"]
        [--language fr|en|auto] [--multilingual] [--no-vad] [--route fr,en [--gap 0.3] [--dual]]

Affiche le texte horodaté, la durée traitée et le temps de calcul (facteur temps réel).
Hors ligne, comme le worker. Les modèles se copient dans `TRANSCRIPTION_MODEL_DIR` (`--model` : nom
du sous-dossier ou chemin).
"""

from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Callable
from pathlib import Path

from visio.config import ConfigurationError, get_settings
from visio.services.transcription import TranscriptionEngineError
from visio.services.whisper_engine import WhisperTranscriber


def resolve_model(name: str, model_dir: str) -> str:
    """Un nom de sous-dossier de `model_dir`, sinon la valeur telle quelle."""
    candidate = Path(model_dir) / name
    return str(candidate) if candidate.is_dir() else name


def main(argv: list[str] | None = None, *, out: Callable[[str], None] = print) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", type=Path)
    parser.add_argument("--model", help="modèle (défaut : TRANSCRIPTION_MODEL)")
    parser.add_argument("--vocab", help="termes à reconnaître (défaut : TRANSCRIPTION_VOCABULARY)")
    parser.add_argument(
        "--language", default="fr", help="fr (défaut), en, ... ou auto (détection par le moteur)"
    )
    parser.add_argument(
        "--multilingual", action="store_true", help="détecter la langue à chaque segment"
    )
    parser.add_argument(
        "--route",
        metavar="fr,en",
        help="langue choisie PAR PHRASE parmi celles-ci (ex. fr,en) ; ignore --language",
    )
    parser.add_argument(
        "--gap", type=float, default=0.3, help="avec --route : pause (s) qui coupe un morceau"
    )
    parser.add_argument(
        "--dual",
        action="store_true",
        help="avec --route : transcrire chaque morceau dans chaque langue, garder la plus sûre",
    )
    parser.add_argument("--no-vad", action="store_true", help="désactiver le filtre de silence")
    args = parser.parse_args(argv)
    try:
        settings = get_settings()
    except ConfigurationError as error:
        out(str(error))
        return 1
    if not args.audio.is_file():
        out(f"fichier introuvable : {args.audio}")
        return 1
    model = (
        resolve_model(args.model, settings.transcription_model_dir)
        if args.model
        else (settings.transcription_model)
    )
    vocabulary = settings.transcription_vocabulary if args.vocab is None else args.vocab
    engine = WhisperTranscriber(
        model,
        model_dir=settings.transcription_model_dir,
        device=settings.transcription_device,
        compute_type=settings.transcription_compute_type,
        vocabulary=vocabulary,
    )
    try:
        started = time.perf_counter()
        engine.load()
        loaded = time.perf_counter()
        if args.route:
            candidates = tuple(c.strip() for c in args.route.split(",") if c.strip())
            pieces = engine.transcribe_routed(
                args.audio, candidates=candidates, max_gap_seconds=args.gap, dual=args.dual
            )
        else:
            pieces = engine.transcribe(
                args.audio,
                language=None if args.language == "auto" else args.language,
                multilingual=args.multilingual,
                vad=not args.no_vad,
            )
        done = time.perf_counter()
    except TranscriptionEngineError as error:
        out(f"ÉCHEC : {error}")
        return 1
    out(f"modèle : {model}   vocabulaire : {vocabulary or '(aucun)'}")
    out(f"langue : {args.language}   multilingue : {args.multilingual}   VAD : {not args.no_vad}")
    for piece in pieces:
        out(f"[{piece.start:7.1f} -> {piece.end:7.1f}] {piece.text.strip()}")
    spoken = pieces[-1].end if pieces else 0.0
    compute = done - loaded
    out(
        f"chargement {loaded - started:.1f} s, calcul {compute:.1f} s pour {spoken:.0f} s de parole"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
