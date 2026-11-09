from __future__ import annotations

from pathlib import Path

from visio.tools import wer


def test_identical_texts_have_no_error(tmp_path: Path) -> None:
    ref = tmp_path / "ref.txt"
    ref.write_text("Bonjour, le RAG et NLP.", encoding="utf-8")
    hyp = tmp_path / "hyp.txt"
    hyp.write_text("[    0.4 ->     8.4] bonjour le rag et nlp", encoding="utf-8")
    lines: list[str] = []
    assert wer.main([str(ref), str(hyp), "--terms", "RAG, NLP"], out=lines.append) == 0
    assert lines[0].startswith("WER 0.0 %")
    assert "RAG                  1 / 1" in lines[2]


def test_errors_are_counted_and_missing_terms_are_reported(tmp_path: Path) -> None:
    ref = tmp_path / "ref.txt"
    ref.write_text("le rag est utile", encoding="utf-8")
    hyp = tmp_path / "hyp.txt"
    hyp.write_text("le rac est", encoding="utf-8")
    lines: list[str] = []
    wer.main([str(ref), str(hyp), "--terms", "RAG"], out=lines.append)
    assert "1 substitués, 1 oubliés, 0 ajoutés" in lines[0]
    assert "RAG                  0 / 1" in lines[2]


def test_a_powershell_utf16_file_is_read(tmp_path: Path) -> None:
    path = tmp_path / "out.txt"
    path.write_bytes("modèle : x\n[ 0.0 -> 1.0] été\n".encode("utf-16"))
    assert wer.words(wer.clean_transcript(wer.read_text(path))) == ["ete"]


def test_show_prints_both_texts_with_the_differences_marked() -> None:
    from visio.tools.wer import diff_ops, render_side_by_side, substitutions, visible_tokens

    ref = visible_tokens("On utilise Airflow et Grafana demain.")
    hyp = visible_tokens("On utilise air flow et Grafana.")
    ops = diff_ops(ref, hyp)
    shown = render_side_by_side(ops)
    assert "[Airflow]" in shown and "[air]" in shown and "[flow]" in shown
    assert "Grafana" in shown and "[Grafana]" not in shown
    assert any(kind == "-" for kind, _, _ in ops)  # « demain » oublié
    assert any(pair.startswith("Airflow ->") for pair in substitutions(ops))
