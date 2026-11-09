"""En-tête HTTP `Range` d'un téléchargement (RFC 9110 §14.2) : logique pure, sans dépendance.

Pourquoi gérer `Range` : un enregistrement fait des centaines de Mo. Sans reprise, une coupure
réseau à 90 % oblige à tout retélécharger, et un lecteur vidéo ne peut pas se positionner.

Règles suivies (une seule plage `bytes=` est servie) :
- `bytes=a-b`, `bytes=a-` et `bytes=-n` (les `n` derniers octets) sont servis en `206` ;
- une plage qui commence au-delà de la fin du fichier est **insatisfaisable** (`416`) ;
- tout le reste (unité inconnue, plusieurs plages, syntaxe invalide, `b < a`) est **ignoré** :
  le fichier entier est servi en `200`. C'est ce que la RFC autorise et ce que font les serveurs
  courants ; refuser ces requêtes casserait des clients valides sans protéger quoi que ce soit.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from visio.domain.errors import RangeNotSatisfiableError

_MAX_HEADER_LENGTH = 100
_RANGE = re.compile(r"bytes=(\d*)-(\d*)", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class ByteRange:
    start: int
    end: int  # inclus

    @property
    def length(self) -> int:
        return self.end - self.start + 1


def parse_range(header: str | None, size: int) -> ByteRange | None:
    """Plage à servir pour un fichier de `size` octets, ou `None` pour servir le fichier entier.

    Lève `RangeNotSatisfiableError` (avec `size`) si la plage demandée est hors du fichier.
    """
    if header is None:
        return None
    value = header.strip()
    if not value or len(value) > _MAX_HEADER_LENGTH:
        return None
    match = _RANGE.fullmatch(value)
    if match is None:
        return None  # unité inconnue, plusieurs plages, syntaxe invalide : ignoré
    first, last = match.groups()
    if not first and not last:
        return None

    if not first:  # suffixe : les `n` derniers octets
        wanted = int(last)
        if wanted == 0 or size == 0:
            raise _unsatisfiable(size)
        return ByteRange(max(size - wanted, 0), size - 1)

    start = int(first)
    if last and int(last) < start:
        return None  # `bytes=10-5` : syntaxe invalide, ignorée
    if start >= size:
        raise _unsatisfiable(size)
    end = size - 1 if not last else min(int(last), size - 1)
    return ByteRange(start, end)


def _unsatisfiable(size: int) -> RangeNotSatisfiableError:
    return RangeNotSatisfiableError(
        f"plage d'octets hors du fichier (taille : {size} octets)", size=size
    )
