"""Moteur de reconnaissance vocale : faster-whisper, **local** (étape 16c).

Le seul module qui connaît faster-whisper (import paresseux : l'API et les tests n'en ont pas
besoin). Aucun accès réseau : le modèle doit déjà être sur le disque, et on le charge avec
`local_files_only=True` et le mode hors-ligne de Hugging Face. Si le modèle manque, c'est une
erreur claire, jamais un téléchargement silencieux.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from visio.config import Settings
from visio.services.transcription import RawSegment, TranscriptionEngineError

SAMPLE_RATE = 16000


def merge_spans(
    spans: list[dict[str, int]], *, max_seconds: float = 25.0, max_gap_seconds: float = 0.7
) -> list[tuple[int, int]]:
    """Regroupe les plages de parole (en échantillons) en morceaux d'au plus `max_seconds`.

    Deux plages séparées de moins de `max_gap_seconds` restent ensemble : une phrase n'est pas
    coupée au milieu, et la langue se détecte sur assez de matière (Whisper lit au plus 30 s)."""
    chunks: list[tuple[int, int]] = []
    for span in spans:
        start, end = int(span["start"]), int(span["end"])
        if chunks:
            last_start, last_end = chunks[-1]
            joined_ok = (end - last_start) / SAMPLE_RATE <= max_seconds
            if (start - last_end) / SAMPLE_RATE <= max_gap_seconds and joined_ok:
                chunks[-1] = (last_start, end)
                continue
        chunks.append((start, end))
    return chunks


def mean_logprob(segments: list[Any]) -> float:
    """Confiance moyenne du moteur dans sa transcription (pondérée par la durée). Plus c'est proche
    de 0, plus il est sûr ; une transcription vide vaut le pire score."""
    total = sum(max(float(s.end) - float(s.start), 0.0) for s in segments)
    if not segments or total <= 0:
        return float("-inf")
    return (
        sum(float(s.avg_logprob) * max(float(s.end) - float(s.start), 0.0) for s in segments)
        / total
    )


def pick_language(model: Any, piece: Any, candidates: tuple[str, ...]) -> str:
    """La langue la plus probable **parmi les candidates**.

    Le moteur en connaît une centaine : on se limite à celles de la réunion, sinon un accent ou un
    mot isolé donne une langue absurde."""
    _best, _prob, scores = model.detect_language(audio=piece)
    ranked = {str(language): float(score) for language, score in scores}
    return max(candidates, key=lambda language: ranked.get(language, 0.0))


class WhisperTranscriber:
    def __init__(
        self,
        model: str,
        *,
        model_dir: str,
        device: str = "auto",
        compute_type: str = "int8",
        vocabulary: str = "",
        languages: tuple[str, ...] = (),
        dual: bool = False,
    ) -> None:
        self.model = model
        self.model_dir = model_dir
        self.device = device
        self.compute_type = compute_type
        self.vocabulary = vocabulary
        self.languages = languages  # 2 langues ou plus : une langue par phrase
        self.dual = dual
        self._loaded: Any = None

    @classmethod
    def from_settings(cls, settings: Settings) -> WhisperTranscriber:
        return cls(
            settings.transcription_model,
            model_dir=settings.transcription_model_dir,
            device=settings.transcription_device,
            compute_type=settings.transcription_compute_type,
            vocabulary=settings.transcription_vocabulary,
            languages=tuple(
                code.strip() for code in settings.transcription_languages.split(",") if code.strip()
            ),
            dual=settings.transcription_dual,
        )

    def load(self) -> Any:
        """Charge le modèle une seule fois (long : plusieurs secondes)."""
        if self._loaded is not None:
            return self._loaded
        # Garde-fou en plus de `local_files_only` : aucune bibliothèque ne doit sortir sur Internet.
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
        try:
            from faster_whisper import WhisperModel
        except ImportError:
            msg = "faster-whisper n'est pas installé (uv sync --extra transcription)"
            raise TranscriptionEngineError(msg) from None
        # Un modèle copié à la main (`data/models/whisper/large-v3`) est un dossier simple, pas un
        # cache Hugging Face : si le nom correspond à un sous-dossier, on charge ce dossier.
        candidate = Path(self.model_dir) / self.model
        name = str(candidate) if candidate.is_dir() else self.model
        try:
            self._loaded = WhisperModel(
                name,
                device=self.device,
                compute_type=self.compute_type,
                download_root=self.model_dir,
                local_files_only=True,
            )
        except Exception as error:  # modèle absent ou illisible : erreur de configuration
            msg = (
                f"modèle « {self.model} » introuvable ou illisible dans {self.model_dir} : {error}"
            )
            raise TranscriptionEngineError(msg) from None
        return self._loaded

    def prompt(self) -> str | None:
        """Contexte donné au moteur : une phrase française qui contient les termes anglais.

        Sans cela, Whisper « francise » les mots anglais (Airflow devient « air link »)."""
        terms = [t.strip() for t in self.vocabulary.split(",") if t.strip()]
        if not terms:
            return None
        return "Cours en français avec des termes techniques anglais : " + ", ".join(terms) + "."

    def transcribe(
        self,
        audio: Path,
        *,
        language: str | None,
        multilingual: bool = False,
        vad: bool = True,
    ) -> list[RawSegment]:
        """`language=None` : langue détectée par le moteur. `multilingual=True` : détectée à
        chaque segment (une phrase anglaise dans une réunion française n'est plus forcée en
        français). Les défauts reproduisent le comportement d'origine (français forcé, VAD)."""
        if language is not None and len(self.languages) > 1:
            return self.transcribe_routed(audio, candidates=self.languages, dual=self.dual)
        model = self.load()
        extra: dict[str, Any] = {"multilingual": True} if multilingual else {}
        try:
            # VAD : une piste de micro est surtout du silence ; sans filtre, Whisper l'invente.
            found, _info = model.transcribe(
                str(audio),
                language=language,
                vad_filter=vad,
                beam_size=5,
                condition_on_previous_text=False,
                initial_prompt=self.prompt(),
                **extra,
            )
            return [RawSegment(float(s.start), float(s.end), str(s.text)) for s in found]
        except Exception as error:
            msg = f"échec de la reconnaissance vocale : {error}"
            raise TranscriptionEngineError(msg) from None

    def transcribe_routed(
        self,
        audio: Path,
        *,
        candidates: tuple[str, ...] = ("fr", "en"),
        max_gap_seconds: float = 0.3,
        dual: bool = False,
    ) -> list[RawSegment]:
        """Une langue **par phrase** : parole découpée par VAD, langue choisie entre `candidates`,
        puis transcription de chaque morceau dans sa langue. Aucun terme n'est donné au moteur.

        `max_gap_seconds` : deux plages de parole plus proches que cela restent ensemble ; une
        valeur trop grande colle deux personnes (donc deux langues) dans un même morceau.
        `dual` : chaque morceau est transcrit dans CHAQUE langue candidate et on garde celle où le
        moteur est le plus sûr de lui (deux fois plus de calcul, sans détection à deviner)."""
        model = self.load()
        try:
            from faster_whisper.audio import decode_audio
            from faster_whisper.vad import VadOptions, get_speech_timestamps

            wave = decode_audio(str(audio), sampling_rate=SAMPLE_RATE)
            spans = get_speech_timestamps(wave, VadOptions(min_silence_duration_ms=500))
            segments: list[RawSegment] = []
            for start, end in merge_spans(spans, max_gap_seconds=max_gap_seconds):
                piece = wave[start:end]
                languages = candidates if dual else (pick_language(model, piece, candidates),)
                best: list[Any] = []
                best_score = float("-inf")
                for language in languages:
                    found, _info = model.transcribe(
                        piece,
                        language=language,
                        vad_filter=False,
                        beam_size=5,
                        condition_on_previous_text=False,
                        initial_prompt=self.prompt(),
                    )
                    kept = list(found)
                    score = mean_logprob(kept)
                    if not best or score > best_score:
                        best, best_score = kept, score
                offset = start / SAMPLE_RATE
                segments.extend(
                    RawSegment(offset + float(s.start), offset + float(s.end), str(s.text))
                    for s in best
                )
            return segments
        except TranscriptionEngineError:
            raise
        except Exception as error:
            msg = f"échec de la reconnaissance vocale : {error}"
            raise TranscriptionEngineError(msg) from None
