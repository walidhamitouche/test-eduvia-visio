"""Mesure l'erreur d'une transcription par rapport à un texte de référence (étape 16d).

    uv run task wer reference.txt resultat.txt [--terms "RAG, NLP, Airflow"] [--show]

`resultat.txt` peut être la sortie de `transcribe-file` (les horodatages `[ 0.4 -> 8.4 ]` et les
lignes de mesure sont ignorés) ou un texte simple. Affiche le WER global (mots substitués,
supprimés, insérés) et, pour chaque terme technique, combien de fois il est dit et combien de fois
il est retrouvé. Hors ligne, sans dépendance.
"""

from __future__ import annotations

import argparse
import re
import sys
import textwrap
import unicodedata
from collections.abc import Callable
from pathlib import Path

_STAMP = re.compile(r"^\s*\[\s*[\d.]+\s*->\s*[\d.]+\s*\]\s*")
_IGNORED = ("modèle :", "langue :", "chargement ", "fichier introuvable", "ÉCHEC")


def read_text(path: Path) -> str:
    raw = path.read_bytes()
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):  # PowerShell `>` écrit en UTF-16
        return raw.decode("utf-16")
    return raw.decode("utf-8-sig")


def clean_transcript(text: str) -> str:
    """Retire horodatages et lignes de mesure d'une sortie de `transcribe-file`."""
    lines = []
    for line in text.splitlines():
        if line.lstrip().startswith(_IGNORED):
            continue
        lines.append(_STAMP.sub("", line))
    return " ".join(lines)


def words(text: str) -> list[str]:
    """Minuscules, sans accents ni ponctuation (l'apostrophe sépare : « l'UMMTO » → l ummto)."""
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", text).split()


def align(ref: list[str], hyp: list[str]) -> tuple[int, int, int]:
    """(substitutions, suppressions, insertions) du chemin d'édition minimal."""
    rows, cols = len(ref) + 1, len(hyp) + 1
    cost = [[0] * cols for _ in range(rows)]
    for i in range(rows):
        cost[i][0] = i
    for j in range(cols):
        cost[0][j] = j
    for i in range(1, rows):
        for j in range(1, cols):
            same = 0 if ref[i - 1] == hyp[j - 1] else 1
            cost[i][j] = min(cost[i - 1][j - 1] + same, cost[i - 1][j] + 1, cost[i][j - 1] + 1)
    i, j, sub, dele, ins = len(ref), len(hyp), 0, 0, 0
    while i > 0 or j > 0:
        if i > 0 and j > 0 and cost[i][j] == cost[i - 1][j - 1] + (ref[i - 1] != hyp[j - 1]):
            sub += ref[i - 1] != hyp[j - 1]
            i, j = i - 1, j - 1
        elif i > 0 and cost[i][j] == cost[i - 1][j] + 1:
            dele += 1
            i -= 1
        else:
            ins += 1
            j -= 1
    return sub, dele, ins


def visible_tokens(text: str) -> list[tuple[str, str]]:
    """(mot tel qu'écrit, mot comparable) : le texte reste lisible, la comparaison l'ignore."""
    pairs = []
    for raw in text.split():
        key = "".join(words(raw))
        if key:
            pairs.append((raw, key))
    return pairs


def diff_ops(
    ref: list[tuple[str, str]], hyp: list[tuple[str, str]]
) -> list[tuple[str, str | None, str | None]]:
    """Chemin d'édition minimal : ("=" | "~" | "-" | "+", mot de référence, mot du résultat)."""
    rows, cols = len(ref) + 1, len(hyp) + 1
    cost = [[0] * cols for _ in range(rows)]
    for i in range(rows):
        cost[i][0] = i
    for j in range(cols):
        cost[0][j] = j
    for i in range(1, rows):
        for j in range(1, cols):
            same = 0 if ref[i - 1][1] == hyp[j - 1][1] else 1
            cost[i][j] = min(cost[i - 1][j - 1] + same, cost[i - 1][j] + 1, cost[i][j - 1] + 1)
    ops: list[tuple[str, str | None, str | None]] = []
    i, j = len(ref), len(hyp)
    while i > 0 or j > 0:
        if i > 0 and j > 0:
            same = ref[i - 1][1] == hyp[j - 1][1]
            if cost[i][j] == cost[i - 1][j - 1] + (not same):
                ops.append(("=" if same else "~", ref[i - 1][0], hyp[j - 1][0]))
                i, j = i - 1, j - 1
                continue
        if i > 0 and cost[i][j] == cost[i - 1][j] + 1:
            ops.append(("-", ref[i - 1][0], None))
            i -= 1
        else:
            ops.append(("+", None, hyp[j - 1][0]))
            j -= 1
    ops.reverse()
    return ops


def render_side_by_side(ops: list[tuple[str, str | None, str | None]], width: int = 100) -> str:
    """Deux textes complets ; les différences sont entre [crochets]."""
    ref_words: list[str] = []
    hyp_words: list[str] = []
    for kind, r, h in ops:
        if kind == "=":
            ref_words.append(str(r))
            hyp_words.append(str(h))
        else:
            if r is not None:
                ref_words.append(f"[{r}]")
            if h is not None:
                hyp_words.append(f"[{h}]")
    wrap = textwrap.TextWrapper(width=width)
    return (
        "RÉFÉRENCE (ce qui a été dit ; [mot] = oublié ou changé) :\n"
        + wrap.fill(" ".join(ref_words))
        + "\n\nRÉSULTAT (ce qui a été transcrit ; [mot] = ajouté ou changé) :\n"
        + wrap.fill(" ".join(hyp_words))
    )


def substitutions(ops: list[tuple[str, str | None, str | None]]) -> list[str]:
    return [f"{r} -> {h}" for kind, r, h in ops if kind == "~"]


def count_term(term: str, tokens: list[str]) -> int:
    target = words(term)
    if not target:
        return 0
    size = len(target)
    return sum(1 for k in range(len(tokens) - size + 1) if tokens[k : k + size] == target)


def main(argv: list[str] | None = None, *, out: Callable[[str], None] = print) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reference", type=Path)
    parser.add_argument("result", type=Path)
    parser.add_argument("--terms", default="", help="termes techniques, séparés par des virgules")
    parser.add_argument(
        "--show", action="store_true", help="afficher les deux textes et les différences"
    )
    args = parser.parse_args(argv)
    for path in (args.reference, args.result):
        if not path.is_file():
            out(f"fichier introuvable : {path}")
            return 1
    ref = words(clean_transcript(read_text(args.reference)))
    hyp = words(clean_transcript(read_text(args.result)))
    if not ref:
        out("référence vide")
        return 1
    sub, dele, ins = align(ref, hyp)
    out(
        f"WER {100 * (sub + dele + ins) / len(ref):.1f} %   "
        f"({len(ref)} mots de référence : {sub} substitués, {dele} oubliés, {ins} ajoutés)"
    )
    if args.show:
        ops = diff_ops(
            visible_tokens(clean_transcript(read_text(args.reference))),
            visible_tokens(clean_transcript(read_text(args.result))),
        )
        out("")
        out(render_side_by_side(ops))
        changed = substitutions(ops)
        if changed:
            out("\nMots changés (dit -> transcrit) : " + " | ".join(changed))
        out("")
    terms = [t.strip() for t in args.terms.split(",") if t.strip()]
    if terms:
        out("termes techniques (retrouvés / dits) :")
        found_all = said_all = 0
        for term in terms:
            said = count_term(term, ref)
            found = min(count_term(term, hyp), said)
            found_all, said_all = found_all + found, said_all + said
            out(f"  {term:<20} {found} / {said}")
        if said_all:
            share = 100 * found_all / said_all
            out(f"  total                {found_all} / {said_all}  ({share:.0f} %)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
