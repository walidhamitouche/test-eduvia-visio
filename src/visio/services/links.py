"""Liens et jetons (besoin §3 et §6, étape 5) : qui peut entrer, avec quel rôle.

Un lien = une ligne `links` + un jeton JWT signé (HS256) placé dans l'URL `<domaine>/j/<jeton>`.

Principes :
- **La base est la source de vérité.** Le jeton prouve que l'URL a été émise par nous (signature) ;
  le rôle, le nom et la réunion sont relus en base à chaque connexion et doivent concorder avec
  le jeton. Un jeton ne donne jamais un droit que la base ne confirme pas.
- **Pas d'expiration dans le jeton.** Les liens ne sont renvoyés qu'une fois et `PATCH` peut
  déplacer le créneau : une date figée dans le jeton deviendrait fausse. Le moment où l'on peut
  entrer se décide côté serveur, avec la règle du créneau (`visio.domain.slot`), à chaque
  connexion. Après la réunion, le lien reste reconnu pour afficher « réunion terminée ».
- **Rien de devinable.** L'URL ne contient que le jeton. Chaque lien (organisateur compris) a
  un `jti` aléatoire de 128 bits, indépendant de la réunion et des autres liens ; seule la
  signature HMAC permet d'en émettre un valable, donc un lien organisateur ne se déduit pas d'un
  lien participant.
- Seul HS256 est accepté (pas de `none`, pas d'autre algorithme), l'encodage doit être canonique
  (un caractère modifié, même sans effet sur les octets décodés, est refusé), et la taille est
  plafonnée avant tout décodage.

Changer `LINK_SIGNING_SECRET` invalide tous les liens déjà émis.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import jwt
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from visio.config import Settings
from visio.db.models import LinkRow, MeetingRow
from visio.domain.errors import (
    InvalidLinkError,
    LinkRevokedError,
    MeetingCancelledError,
    MeetingEndedError,
    TooManyOrganizersError,
)
from visio.domain.meeting import SlotStatus
from visio.domain.roles import Role
from visio.domain.slot import status_at
from visio.services.meetings import get_meeting

ALGORITHM = "HS256"
MAX_ORGANIZER_LINKS = 10  # DECISIONS.md point 12
# Un jeton courant fait ~400 caractères ; le pire cas (nom et identifiant de 200 émojis) ~2,5 Ko.
# Au-delà du plafond, on ne décode même pas.
MAX_TOKEN_LENGTH = 4096
_REQUIRED_CLAIMS = ("jti", "mid", "role")
# message unique : on ne révèle jamais la raison du refus
_INVALID = "lien invalide"


class _Utf8Encoder(json.JSONEncoder):
    """Écrit les accents tels quels (PyJWT les échappe en `\\u00e9`, soit 6 octets au lieu de 2 :
    l'URL d'un nom de 200 lettres accentuées ferait plus de 2 Ko)."""

    def __init__(self, **kwargs: Any) -> None:
        kwargs["ensure_ascii"] = False
        super().__init__(**kwargs)


@dataclass(frozen=True, slots=True)
class ResolvedLink:
    """Lien vérifié : ce que l'étape 6 (`POST /join`) utilise pour décider de l'entrée."""

    link: LinkRow
    meeting: MeetingRow


# --- émission --------------------------------------------------------------------------------


def issue_token(settings: Settings, link: LinkRow) -> str:
    """Jeton signé d'un lien. Déterministe : le même lien donne toujours le même jeton."""
    claims: dict[str, Any] = {
        "jti": link.id,
        "mid": link.meeting_id,
        "role": link.role.value,
        "iat": int(link.created_at.timestamp()),
    }
    if link.display_name is not None:
        claims["name"] = link.display_name
    if link.external_id is not None:
        claims["eid"] = link.external_id
    return jwt.encode(
        claims,
        settings.link_signing_secret.get_secret_value(),
        algorithm=ALGORITHM,
        json_encoder=_Utf8Encoder,
    )


def issue_link_url(settings: Settings, link: LinkRow) -> str:
    """URL publique d'un lien : `<domaine>/j/<jeton>`. Ne contient jamais l'id de la réunion en
    clair (il est dans le jeton, que seul le porteur du lien possède)."""
    return f"{settings.public_base_url.rstrip('/')}/j/{issue_token(settings, link)}"


def create_link(
    db: Session,
    meeting_id: str,
    *,
    role: Role,
    display_name: str,
    external_id: str | None,
    now: datetime,
) -> LinkRow:
    """Crée un lien nominatif (`POST /v1/meetings/:id/links`).

    Refusé si la réunion est annulée ou terminée. Un lien organisateur perdu se remplace en
    créant un nouveau lien ; l'ancien reste valable tant qu'il n'est pas révoqué.
    """
    meeting = get_meeting(db, meeting_id)
    status = status_at(meeting.to_domain(), now)
    if status is SlotStatus.CANCELLED:
        msg = "la réunion est annulée"
        raise MeetingCancelledError(msg)
    if status is SlotStatus.ENDED:
        msg = "la réunion est terminée : aucun lien ne peut plus être créé"
        raise MeetingEndedError(msg)
    if role is Role.ORGANIZER:
        active = db.scalar(
            select(func.count())
            .select_from(LinkRow)
            .where(
                LinkRow.meeting_id == meeting_id,
                LinkRow.role == Role.ORGANIZER,
                LinkRow.revoked_at.is_(None),
            )
        )
        if (active or 0) >= MAX_ORGANIZER_LINKS:
            msg = f"une réunion a au plus {MAX_ORGANIZER_LINKS} liens organisateur actifs"
            raise TooManyOrganizersError(msg)
    link = LinkRow(
        meeting_id=meeting_id, role=role, display_name=display_name, external_id=external_id
    )
    db.add(link)
    db.commit()
    return link


# --- vérification ----------------------------------------------------------------------------


def is_canonical_token(token: str) -> bool:
    """Chaque segment doit être exactement ce que produirait notre encodeur : un base64url sans
    remplissage dont les bits inutilisés sont à zéro (sinon deux chaînes différentes donneraient
    le même jeton)."""
    segments = token.split(".")
    if len(segments) != 3:
        return False
    for segment in segments:
        try:
            raw = base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))
        except ValueError:
            return False
        if base64.urlsafe_b64encode(raw).rstrip(b"=").decode() != segment:
            return False
    return True


def decode_token(settings: Settings, token: str) -> dict[str, Any]:
    """Vérifie la signature et la forme du jeton, sans toucher à la base.

    Lève `InvalidLinkError` pour tout jeton falsifié, inventé, mal formé ou d'un autre algorithme.
    """
    if not token or len(token) > MAX_TOKEN_LENGTH or not token.isascii():
        raise InvalidLinkError(_INVALID)
    try:
        if not is_canonical_token(token):
            raise InvalidLinkError(_INVALID)
        claims: dict[str, Any] = jwt.decode(
            token,
            settings.link_signing_secret.get_secret_value(),
            algorithms=[ALGORITHM],
            options={"require": list(_REQUIRED_CLAIMS), "verify_exp": False, "verify_iat": False},
        )
    except (jwt.PyJWTError, ValueError):
        raise InvalidLinkError(_INVALID) from None
    if not all(isinstance(claims[name], str) for name in _REQUIRED_CLAIMS):
        raise InvalidLinkError(_INVALID)
    return claims


def resolve_link(db: Session, settings: Settings, token: str) -> ResolvedLink:
    """Jeton -> lien et réunion, ou une erreur. À appeler à **chaque** connexion.

    1. signature et forme (sans base de données : une URL inventée ne coûte rien) ;
    2. le lien existe en base, appartient à la réunion annoncée, avec le même rôle, le même nom
       et le même identifiant externe que dans le jeton ;
    3. le lien n'est pas révoqué.

    Ne regarde pas l'heure : l'ouverture, la fin et l'annulation relèvent de la règle du créneau
    (étape 6). Toutes les erreurs de forme donnent le même `invalid_link`.
    """
    claims = decode_token(settings, token)
    link = db.get(LinkRow, claims["jti"])
    if (
        link is None
        or link.meeting_id != claims["mid"]
        or link.role.value != claims["role"]
        or link.display_name != claims.get("name")
        or link.external_id != claims.get("eid")
    ):
        raise InvalidLinkError(_INVALID)
    if link.revoked_at is not None:
        msg = "ce lien a été révoqué : le participant a été exclu"
        raise LinkRevokedError(msg)
    meeting = db.get(MeetingRow, link.meeting_id)
    if meeting is None:  # ne peut pas arriver (clé étrangère), mais on ne devine rien
        raise InvalidLinkError(_INVALID)
    return ResolvedLink(link=link, meeting=meeting)


def revoke_link(db: Session, link_id: str, *, now: datetime) -> LinkRow:
    """Révoque un lien (liste de révocation). Rejouable : la première date est conservée.

    Utilisé pour l'exclusion définitive (étape 8). Sur un lien nominatif, l'exclu ne peut plus
    revenir ; sur le lien générique partagé, révoquer exclurait tout le monde, donc l'étape 8
    vise l'identité de la personne (voir DECISIONS.md §6 ter).
    """
    link = db.get(LinkRow, link_id)
    if link is None:
        raise InvalidLinkError(_INVALID)
    if link.revoked_at is None:
        link.revoked_at = now
        db.commit()
    return link


def links_of(db: Session, meeting_id: str) -> Sequence[LinkRow]:
    return db.scalars(
        select(LinkRow).where(LinkRow.meeting_id == meeting_id).order_by(LinkRow.created_at)
    ).all()
