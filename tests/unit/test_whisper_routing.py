from __future__ import annotations

from typing import Any

from visio.services.whisper_engine import SAMPLE_RATE, merge_spans, pick_language


def sec(value: float) -> int:
    return int(value * SAMPLE_RATE)


def test_close_spans_are_merged_into_one_chunk() -> None:
    spans = [{"start": sec(0), "end": sec(3)}, {"start": sec(3.4), "end": sec(6)}]
    assert merge_spans(spans) == [(sec(0), sec(6))]


def test_a_long_pause_starts_a_new_chunk() -> None:
    spans = [{"start": sec(0), "end": sec(3)}, {"start": sec(5), "end": sec(8)}]
    assert merge_spans(spans) == [(sec(0), sec(3)), (sec(5), sec(8))]


def test_a_chunk_never_exceeds_the_limit() -> None:
    spans = [{"start": sec(i * 10), "end": sec(i * 10 + 9.9)} for i in range(4)]
    chunks = merge_spans(spans, max_seconds=25.0)
    assert all((end - start) / SAMPLE_RATE <= 25.0 for start, end in chunks)
    assert len(chunks) == 2


class FakeModel:
    def __init__(self, scores: list[tuple[str, float]]) -> None:
        self.scores = scores

    def detect_language(self, **_: Any) -> tuple[str, float, list[tuple[str, float]]]:
        return self.scores[0][0], self.scores[0][1], self.scores


def test_the_language_is_chosen_among_the_candidates_only() -> None:
    model = FakeModel([("nn", 0.5), ("en", 0.3), ("fr", 0.1)])
    assert pick_language(model, object(), ("fr", "en")) == "en"
    assert pick_language(FakeModel([("fr", 0.9), ("en", 0.05)]), object(), ("fr", "en")) == "fr"


class Seg:
    def __init__(self, start: float, end: float, avg_logprob: float) -> None:
        self.start, self.end, self.avg_logprob = start, end, avg_logprob


def test_mean_logprob_is_weighted_by_duration_and_empty_is_worst() -> None:
    from visio.services.whisper_engine import mean_logprob

    assert mean_logprob([Seg(0, 1, -1.0), Seg(1, 4, -0.2)]) == (-1.0 * 1 + -0.2 * 3) / 4
    assert mean_logprob([]) == float("-inf")
    assert mean_logprob([Seg(0, 5, -0.1)]) > mean_logprob([Seg(0, 5, -0.9)])


def test_with_two_languages_the_engine_routes_by_sentence(tmp_path: Any) -> None:
    from visio.services.whisper_engine import WhisperTranscriber

    calls: list[tuple[tuple[str, ...], bool]] = []

    class Spy(WhisperTranscriber):
        def transcribe_routed(
            self, audio: Any, *, candidates: Any = (), **kwargs: Any
        ) -> list[Any]:
            calls.append((tuple(candidates), bool(kwargs.get("dual"))))
            return []

    Spy("m", model_dir="/m", languages=("fr", "en"), dual=True).transcribe(
        tmp_path / "a.ogg", language="fr"
    )
    assert calls == [(("fr", "en"), True)]


def test_one_language_keeps_the_forced_language_path() -> None:
    from visio.config import Settings
    from visio.services.whisper_engine import WhisperTranscriber

    assert Settings.model_fields["transcription_languages"].default == "fr,en"
    assert WhisperTranscriber("m", model_dir="/m").languages == ()
