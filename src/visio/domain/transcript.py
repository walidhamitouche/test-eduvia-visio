"""Transcription d'une réunion : segments, fusion chronologique et formats de sortie (étape 16a).

Logique pure, sans base ni S3 ni modèle de reconnaissance vocale. Le moteur (étape 16c) produit,
**pour chaque participant**, des segments `(début, fin, texte)` ; on y met le nom et l'`external_id`
de la personne (connus par le lien, jamais devinés à partir de la voix), puis on fusionne tout en
une seule chronologie.

Les temps sont des **secondes depuis le début de l'enregistrement** ; le document JSON porte en plus
l'instant absolu de ce début (`started_at`) pour retrouver l'heure réelle d'une phrase.
Les sorties : JSON (la référence), texte brut, SRT, VTT.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

FORMATS = ("json", "txt", "srt", "vtt")
SCHEMA_VERSION = 1

_SPACES = re.compile(r"\s+")


@dataclass(frozen=True, slots=True)
class Segment:
    """Une phrase (ou un morceau de phrase) dite par une personne."""

    start: float  # secondes depuis le début de l'enregistrement
    end: float
    speaker: str  # nom affiché (celui du lien)
    external_id: str | None
    text: str


def clean_text(text: str) -> str:
    """Une ligne, espaces normalisés (une ligne vide terminerait un sous-titre SRT/VTT)."""
    return _SPACES.sub(" ", text).strip()


def normalize(segment: Segment) -> Segment | None:
    """Segment propre, ou `None` s'il n'y a rien à garder (texte vide, temps négatifs ou absurdes).

    - texte réduit à une ligne ;
    - début ramené à 0 s s'il est négatif ;
    - fin jamais avant le début.
    """
    text = clean_text(segment.text)
    if not text:
        return None
    start = max(segment.start, 0.0)
    end = max(segment.end, start)
    return Segment(start, end, clean_text(segment.speaker) or "?", segment.external_id, text)


def merge(streams: Iterable[Iterable[Segment]]) -> list[Segment]:
    """Fusionne les segments de plusieurs locuteurs dans l'ordre chronologique.

    Tri par début, puis par fin, puis par nom : le résultat ne dépend **pas** de l'ordre dans
    lequel les participants ont été traités (reprise après interruption : même résultat, sans
    doublon ni désordre). Les segments vides sont écartés.
    """
    kept = [n for stream in streams for segment in stream if (n := normalize(segment)) is not None]
    kept.sort(key=lambda s: (s.start, s.end, s.speaker, s.external_id or "", s.text))
    return kept


def speakers(segments: Sequence[Segment]) -> list[tuple[str, str | None]]:
    """Les locuteurs qui ont parlé, dans l'ordre de leur première phrase (sans doublon)."""
    seen: dict[tuple[str, str | None], None] = {}
    for segment in segments:
        seen.setdefault((segment.speaker, segment.external_id), None)
    return list(seen)


# --- JSON ------------------------------------------------------------------------------------


def to_document(
    segments: Sequence[Segment],
    *,
    language: str,
    started_at: str | None,
    duration_seconds: float | None = None,
) -> dict[str, Any]:
    """Le document JSON de référence (ce qui est stocké sur le S3). `started_at` : ISO 8601."""
    if duration_seconds is None:
        duration_seconds = max((s.end for s in segments), default=0.0)
    return {
        "version": SCHEMA_VERSION,
        "language": language,
        "started_at": started_at,
        "duration_seconds": round(duration_seconds, 3),
        "speakers": [{"display_name": n, "external_id": e} for n, e in speakers(segments)],
        "segments": [
            {
                "start": round(s.start, 3),
                "end": round(s.end, 3),
                "speaker": s.speaker,
                "external_id": s.external_id,
                "text": s.text,
            }
            for s in segments
        ],
    }


def from_document(document: dict[str, Any]) -> list[Segment]:
    """Relit les segments d'un document JSON stocké."""
    return [
        Segment(
            float(item["start"]),
            float(item["end"]),
            str(item["speaker"]),
            item.get("external_id"),
            str(item["text"]),
        )
        for item in document.get("segments", [])
    ]


# --- texte, SRT, VTT -------------------------------------------------------------------------


def _milliseconds(seconds: float) -> int:
    return round(seconds * 1000)  # entier : pas de « 59,9996 » qui s'afficherait 00:00:59,1000


def _timestamp(seconds: float, separator: str) -> str:
    total = _milliseconds(seconds)
    hours, rest = divmod(total, 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    secs, millis = divmod(rest, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}{separator}{millis:03d}"


def to_text(segments: Sequence[Segment]) -> str:
    """Texte brut lisible : `[hh:mm:ss] Nom : phrase`, une ligne par segment."""
    lines = [f"[{_timestamp(s.start, ',')[:8]}] {s.speaker} : {s.text}" for s in segments]
    return "".join(f"{line}\n" for line in lines)


def to_srt(segments: Sequence[Segment]) -> str:
    """SRT : numéro, `hh:mm:ss,mmm --> hh:mm:ss,mmm`, `Nom : phrase`, ligne vide."""
    blocks = []
    for index, s in enumerate(segments, start=1):
        times = f"{_timestamp(s.start, ',')} --> {_timestamp(s.end, ',')}"
        blocks.append(f"{index}\n{times}\n{s.speaker} : {s.text}\n")
    return "\n".join(blocks)


def _vtt_escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def to_vtt(segments: Sequence[Segment]) -> str:
    """WebVTT : en-tête, puis `hh:mm:ss.mmm --> hh:mm:ss.mmm` et `<v Nom>phrase`.

    Le texte est échappé (`&`, `<`, `>`) : une phrase contenant `-->` ou `<` ne doit pas casser
    le fichier.
    """
    blocks = ["WEBVTT\n"]
    for s in segments:
        times = f"{_timestamp(s.start, '.')} --> {_timestamp(s.end, '.')}"
        # `-->` dans le texte est neutralisé par l'échappement de `>`.
        blocks.append(f"{times}\n<v {_vtt_escape(s.speaker)}>{_vtt_escape(s.text)}\n")
    return "\n".join(blocks)


def render(segments: Sequence[Segment], fmt: str) -> str:
    """Texte, SRT ou VTT (le JSON passe par `to_document`)."""
    if fmt == "txt":
        return to_text(segments)
    if fmt == "srt":
        return to_srt(segments)
    if fmt == "vtt":
        return to_vtt(segments)
    msg = f"format inconnu : {fmt}"
    raise ValueError(msg)
