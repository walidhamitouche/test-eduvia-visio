"""Étape 16a : fusion des segments et formats de sortie (texte, SRT, VTT), logique pure."""

from __future__ import annotations

import re

import pytest

from visio.domain import transcript as t
from visio.domain.transcript import Segment


def seg(start: float, end: float, speaker: str, text: str, ext: str | None = None) -> Segment:
    return Segment(start, end, speaker, ext, text)


MARIE = [
    seg(0.0, 4.0, "Marie Durand", "Bonjour à tous.", "staff_42"),
    seg(20.0, 25.0, "Marie Durand", "On commence.", "staff_42"),
]
PAUL = [
    seg(4.5, 9.0, "Paul Martin", "Bonjour Marie.", "stu_7"),
    seg(12.0, 13.0, "Paul Martin", "Une question.", "stu_7"),
]


# --- fusion ----------------------------------------------------------------------------------


def test_segments_of_several_speakers_are_merged_in_chronological_order() -> None:
    merged = t.merge([MARIE, PAUL])
    assert [s.start for s in merged] == [0.0, 4.5, 12.0, 20.0]
    assert [s.speaker for s in merged] == [
        "Marie Durand",
        "Paul Martin",
        "Paul Martin",
        "Marie Durand",
    ]


def test_the_merge_does_not_depend_on_the_order_the_speakers_were_processed() -> None:
    """Reprise après interruption : traiter les participants dans un autre ordre = même résultat."""
    assert t.merge([MARIE, PAUL]) == t.merge([PAUL, MARIE])
    assert t.merge([MARIE, PAUL]) == t.merge([PAUL[:1], MARIE, PAUL[1:]])


def test_simultaneous_segments_have_a_stable_order() -> None:
    a, b = seg(1.0, 2.0, "Zoé", "a"), seg(1.0, 2.0, "Alice", "b")
    assert [s.speaker for s in t.merge([[a], [b]])] == ["Alice", "Zoé"]
    assert [s.speaker for s in t.merge([[b], [a]])] == ["Alice", "Zoé"]


def test_empty_and_blank_segments_are_dropped() -> None:
    merged = t.merge([[seg(0, 1, "A", ""), seg(1, 2, "A", "   \n "), seg(2, 3, "A", "ok")]])
    assert [s.text for s in merged] == ["ok"]


def test_nothing_said_gives_an_empty_transcript() -> None:
    assert t.merge([]) == []
    assert t.merge([[], []]) == []


def test_negative_start_and_inverted_end_are_repaired() -> None:
    (s,) = t.merge([[seg(-0.3, -1.0, "A", "x")]])
    assert (s.start, s.end) == (0.0, 0.0)
    (s,) = t.merge([[seg(5.0, 4.0, "A", "x")]])
    assert (s.start, s.end) == (5.0, 5.0)


def test_text_is_reduced_to_one_line() -> None:
    (s,) = t.merge([[seg(0, 1, "A", "  une   phrase\n\nsur   deux lignes ")]])
    assert s.text == "une phrase sur deux lignes"


def test_speakers_are_listed_once_in_order_of_first_phrase() -> None:
    merged = t.merge([MARIE, PAUL])
    assert t.speakers(merged) == [("Marie Durand", "staff_42"), ("Paul Martin", "stu_7")]


# --- JSON ------------------------------------------------------------------------------------


def test_the_json_document_carries_names_and_external_ids() -> None:
    merged = t.merge([MARIE, PAUL])
    document = t.to_document(merged, language="fr", started_at="2027-01-02T16:00:00+01:00")
    assert document["language"] == "fr"
    assert document["duration_seconds"] == 25.0
    assert document["speakers"] == [
        {"display_name": "Marie Durand", "external_id": "staff_42"},
        {"display_name": "Paul Martin", "external_id": "stu_7"},
    ]
    assert document["segments"][1] == {
        "start": 4.5,
        "end": 9.0,
        "speaker": "Paul Martin",
        "external_id": "stu_7",
        "text": "Bonjour Marie.",
    }


def test_the_json_document_round_trips() -> None:
    merged = t.merge([MARIE, PAUL])
    document = t.to_document(merged, language="fr", started_at=None)
    assert t.from_document(document) == merged


# --- texte -----------------------------------------------------------------------------------


def test_plain_text_has_one_line_per_segment() -> None:
    text = t.to_text(t.merge([MARIE, PAUL]))
    assert text.splitlines() == [
        "[00:00:00] Marie Durand : Bonjour à tous.",
        "[00:00:04] Paul Martin : Bonjour Marie.",
        "[00:00:12] Paul Martin : Une question.",
        "[00:00:20] Marie Durand : On commence.",
    ]
    assert t.to_text([]) == ""


# --- SRT / VTT (parseurs de référence, stricts) ------------------------------------------------

SRT_TIME = r"(\d{2}):([0-5]\d):([0-5]\d),(\d{3})"
VTT_TIME = r"(\d{2,}):([0-5]\d):([0-5]\d)\.(\d{3})"


def to_ms(parts: tuple[str, ...]) -> int:
    h, m, s, ms = (int(p) for p in parts)
    return ((h * 60 + m) * 60 + s) * 1000 + ms


def parse_srt(content: str) -> list[tuple[int, int, int, str]]:
    """Parseur strict : blocs séparés par une ligne vide, numéros 1..n, une ligne de texte."""
    cues: list[tuple[int, int, int, str]] = []
    if not content:
        return cues
    assert content.endswith("\n") and not content.endswith("\n\n")
    for expected, block in enumerate(content.rstrip("\n").split("\n\n"), start=1):
        lines = block.split("\n")
        assert len(lines) >= 3, block
        assert int(lines[0]) == expected
        match = re.fullmatch(f"{SRT_TIME} --> {SRT_TIME}", lines[1])
        assert match, lines[1]
        start, end = to_ms(match.groups()[:4]), to_ms(match.groups()[4:])
        assert end >= start
        cues.append((expected, start, end, "\n".join(lines[2:])))
    return cues


def parse_vtt(content: str) -> list[tuple[int, int, str]]:
    assert content.startswith("WEBVTT\n")
    cues: list[tuple[int, int, str]] = []
    blocks = content.rstrip("\n").split("\n\n")[1:]
    for block in blocks:
        lines = block.split("\n")
        match = re.fullmatch(f"{VTT_TIME} --> {VTT_TIME}", lines[0])
        assert match, lines[0]
        start, end = to_ms(match.groups()[:4]), to_ms(match.groups()[4:])
        assert end >= start
        cues.append((start, end, "\n".join(lines[1:])))
    return cues


def test_srt_is_valid_and_numbered_from_one() -> None:
    cues = parse_srt(t.to_srt(t.merge([MARIE, PAUL])))
    assert [c[0] for c in cues] == [1, 2, 3, 4]
    assert cues[1][1:3] == (4500, 9000)
    assert cues[1][3] == "Paul Martin : Bonjour Marie."


def test_srt_times_use_a_comma_and_hours_are_not_wrapped() -> None:
    (cue,) = parse_srt(t.to_srt([seg(3661.25, 7322.5, "A", "tard")]))
    assert cue[1:3] == (3_661_250, 7_322_500)
    assert "01:01:01,250 --> 02:02:02,500" in t.to_srt([seg(3661.25, 7322.5, "A", "tard")])


def test_milliseconds_never_overflow() -> None:
    srt = t.to_srt([seg(59.9996, 60.0004, "A", "x")])
    assert "00:01:00,000 --> 00:01:00,000" in srt


def test_vtt_is_valid_with_a_header_and_voice_tags() -> None:
    cues = parse_vtt(t.to_vtt(t.merge([MARIE, PAUL])))
    assert len(cues) == 4
    assert cues[0] == (0, 4000, "<v Marie Durand>Bonjour à tous.")


def test_vtt_escapes_markup_and_arrows_in_the_text() -> None:
    content = t.to_vtt([seg(0, 1, "A", "si a < b & b --> c")])
    (cue,) = parse_vtt(content)
    assert cue[2] == "<v A>si a &lt; b &amp; b --&gt; c"
    assert "-->" not in cue[2]


def test_empty_transcripts_render_cleanly() -> None:
    assert t.to_srt([]) == ""
    assert t.to_vtt([]) == "WEBVTT\n"
    assert parse_srt("") == []
    assert parse_vtt("WEBVTT\n") == []


def test_render_refuses_an_unknown_format() -> None:
    with pytest.raises(ValueError, match="format inconnu"):
        t.render([], "pdf")
