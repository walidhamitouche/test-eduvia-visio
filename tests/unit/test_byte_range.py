"""En-tête `Range` d'un téléchargement (étape 15) : logique pure, sans réseau ni base."""

from __future__ import annotations

import pytest

from visio.domain.byte_range import ByteRange, parse_range
from visio.domain.errors import RangeNotSatisfiableError

SIZE = 1000


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("bytes=0-499", ByteRange(0, 499)),
        ("bytes=500-999", ByteRange(500, 999)),
        ("bytes=0-0", ByteRange(0, 0)),  # un seul octet
        ("bytes=999-999", ByteRange(999, 999)),  # le dernier octet
        ("bytes=500-", ByteRange(500, 999)),  # jusqu'à la fin
        ("bytes=0-", ByteRange(0, 999)),  # tout, mais en 206
        ("bytes=-100", ByteRange(900, 999)),  # les 100 derniers octets
        ("bytes=-1", ByteRange(999, 999)),
        ("bytes=900-5000", ByteRange(900, 999)),  # fin trop grande : ramenée à la fin du fichier
        ("bytes=-5000", ByteRange(0, 999)),  # suffixe plus long que le fichier : tout le fichier
        ("BYTES=0-9", ByteRange(0, 9)),  # l'unité n'est pas sensible à la casse
        ("  bytes=0-9  ", ByteRange(0, 9)),
    ],
)
def test_a_single_satisfiable_range_is_served_as_a_partial_content(
    header: str, expected: ByteRange
) -> None:
    assert parse_range(header, SIZE) == expected


@pytest.mark.parametrize(
    "header",
    [
        None,
        "",
        "   ",
        "bytes=",
        "bytes=-",
        "bytes=abc-def",
        "bytes=10-5",  # fin avant le début : syntaxe invalide, ignorée (RFC 9110)
        "items=0-10",  # unité inconnue
        "bytes=0-10,20-30",  # plusieurs plages : on sert tout le fichier
        "bytes=0-10, 20-30",
        "0-10",
        "bytes=0-10-20",
        "bytes=-5-",
        "bytes = 0-10",
        "bytes=" + "9" * 200,  # entête démesurée : ignorée avant tout calcul
    ],
)
def test_anything_unreadable_is_ignored_and_the_whole_file_is_served(header: str | None) -> None:
    assert parse_range(header, SIZE) is None


@pytest.mark.parametrize("header", ["bytes=1000-", "bytes=1000-2000", "bytes=5000-", "bytes=-0"])
def test_a_range_outside_the_file_is_unsatisfiable(header: str) -> None:
    with pytest.raises(RangeNotSatisfiableError) as error:
        parse_range(header, SIZE)
    assert error.value.details == {"size": SIZE}  # la taille réelle accompagne le 416


def test_every_range_of_an_empty_file_is_unsatisfiable() -> None:
    for header in ("bytes=0-", "bytes=0-0", "bytes=-1"):
        with pytest.raises(RangeNotSatisfiableError):
            parse_range(header, 0)
    assert parse_range(None, 0) is None


def test_length_counts_both_ends() -> None:
    assert ByteRange(0, 0).length == 1
    assert ByteRange(900, 999).length == 100
