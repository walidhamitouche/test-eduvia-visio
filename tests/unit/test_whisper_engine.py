"""Étape 16c, sécurité : le moteur se charge hors ligne (aucun téléchargement possible)."""

from __future__ import annotations

import os
import sys
import types
from pathlib import Path
from typing import Any

import pytest

from visio.services.transcription import TranscriptionEngineError
from visio.services.whisper_engine import WhisperTranscriber


class _Segment:
    def __init__(self, start: float, end: float, text: str) -> None:
        self.start, self.end, self.text = start, end, text


def fake_module(monkeypatch: pytest.MonkeyPatch, seen: dict[str, Any]) -> None:
    class WhisperModel:
        def __init__(self, name: str, **kwargs: Any) -> None:
            seen["name"], seen["kwargs"] = name, kwargs

        def transcribe(self, path: str, **kwargs: Any) -> tuple[list[_Segment], None]:
            seen["transcribe"] = kwargs
            return [_Segment(0.5, 2.0, " Bonjour à tous ")], None

    module = types.ModuleType("faster_whisper")
    module.WhisperModel = WhisperModel  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "faster_whisper", module)


def test_the_model_is_loaded_from_disk_only(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}
    fake_module(monkeypatch, seen)
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    engine = WhisperTranscriber("medium", model_dir="/models", device="cpu")
    engine.load()
    assert seen["kwargs"]["local_files_only"] is True
    assert seen["kwargs"]["download_root"] == "/models"
    assert os.environ["HF_HUB_OFFLINE"] == "1"


def test_a_model_copied_into_the_model_dir_is_loaded_by_its_folder(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen: dict[str, Any] = {}
    fake_module(monkeypatch, seen)
    (tmp_path / "large-v3").mkdir()
    WhisperTranscriber("large-v3", model_dir=str(tmp_path)).load()
    assert seen["name"] == str(tmp_path / "large-v3")


def test_segments_are_returned_in_french_with_voice_activity_filter(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen: dict[str, Any] = {}
    fake_module(monkeypatch, seen)
    engine = WhisperTranscriber("medium", model_dir="/models")
    (piece,) = engine.transcribe(tmp_path / "a.ogg", language="fr")
    assert (piece.start, piece.end, piece.text) == (0.5, 2.0, " Bonjour à tous ")
    assert seen["transcribe"]["language"] == "fr"
    assert seen["transcribe"]["vad_filter"] is True


def test_the_vocabulary_is_given_to_the_engine_as_french_context(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen: dict[str, Any] = {}
    fake_module(monkeypatch, seen)
    engine = WhisperTranscriber("medium", model_dir="/m", vocabulary=" Airflow, Python ,, Docker ")
    engine.transcribe(tmp_path / "a.ogg", language="fr")
    prompt = seen["transcribe"]["initial_prompt"]
    assert prompt.startswith("Cours en français")
    assert "Airflow, Python, Docker" in prompt


def test_without_vocabulary_no_prompt_is_given(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen: dict[str, Any] = {}
    fake_module(monkeypatch, seen)
    WhisperTranscriber("medium", model_dir="/m").transcribe(tmp_path / "a.ogg", language="fr")
    assert seen["transcribe"]["initial_prompt"] is None


def test_a_missing_model_is_an_error_not_a_download(monkeypatch: pytest.MonkeyPatch) -> None:
    class WhisperModel:
        def __init__(self, name: str, **kwargs: Any) -> None:
            msg = "local_files_only: introuvable"
            raise RuntimeError(msg)

    module = types.ModuleType("faster_whisper")
    module.WhisperModel = WhisperModel  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "faster_whisper", module)
    with pytest.raises(TranscriptionEngineError, match="introuvable"):
        WhisperTranscriber("medium", model_dir="/models").load()
