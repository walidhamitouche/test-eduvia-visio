"""Jetons d'accès LiveKit (étape 6) : droits par rôle, nom de salle et identités opaques.

Un jeton LiveKit est un JWT HS256 signé avec `LIVEKIT_API_SECRET` (format documenté par
LiveKit : `iss` = clé d'API, `sub` = identité, `video` = droits). On le fabrique avec PyJWT,
déjà présent : aucune dépendance de plus pour cette étape. Les ordres du backend vers
LiveKit (couper un micro, exclure) passent par `services.rooms`.

Principes :
- **Courte durée.** Le jeton expire au bout de `LIVEKIT_TOKEN_TTL`, et jamais après la fin
  de la réunion. LiveKit ne vérifie l'expiration qu'à la connexion : une reconnexion après
  expiration repasse par `POST /v1/join`, donc par toutes les vérifications (créneau,
  annulation, exclusion). Un jeton valable jusqu'à `ends_at` permettrait à une personne
  exclue de revenir avec lui.
- **Noms opaques.** La salle et les identités sont dérivées par HMAC d'un secret : rien de
  devinable, rien qui révèle l'id de la réunion ni le `jti` d'un lien.
- **Droits minimaux.** Chacun publie (micro, caméra, écran) et s'abonne. Personne n'est
  `roomAdmin`, personne ne peut créer de salle ni envoyer de données (voir `video_grant`).
"""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timedelta
from typing import Any

import jwt

from visio.domain.roles import Role

ALGORITHM = "HS256"
LIVEKIT_TOKEN_TTL = timedelta(minutes=10)
# Ce qu'une personne peut publier : micro, caméra, partage d'écran (et son).
PUBLISH_SOURCES = ("microphone", "camera", "screen_share", "screen_share_audio")


def _derive(secret: str, label: str, *parts: str) -> str:
    """128 bits dérivés de `secret` par HMAC-SHA256, séparés par `label` (un usage = un label)."""
    message = ":".join((label, *parts)).encode()
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()[:32]


def room_name(link_secret: str, meeting_id: str) -> str:
    """Nom de la salle LiveKit d'une réunion : stable, opaque, sans lien visible avec l'id."""
    return "rm_" + _derive(link_secret, "room", meeting_id)


def named_identity(link_secret: str, link_id: str) -> str:
    """Identité LiveKit d'un lien nominatif : stable (même lien = même identité), sans le `jti`."""
    return "pn_" + _derive(link_secret, "identity-named", link_id)


def generic_identity(link_secret: str, link_id: str, client_id: str) -> str:
    """Identité d'une personne sur le lien générique : propre à son navigateur (`client_id`).

    Le `client_id` reste secret (seul son navigateur le connaît) ; les autres voient seulement
    l'identité dérivée, donc ne peuvent pas la reprendre.
    """
    return "pg_" + _derive(link_secret, "identity-generic", link_id, client_id)


def video_grant(role: Role, room: str) -> dict[str, Any]:
    """Droits LiveKit (besoin §3) : **identiques pour les deux rôles**.

    La modération (couper un micro, exclure, terminer) n'est volontairement *pas* un droit
    LiveKit : un organisateur n'est jamais `roomAdmin`, donc son navigateur ne peut rien
    ordonner au serveur de médias. Il passe par `/v1/room/*`, où le backend vérifie le rôle du
    lien, applique la liste d'exclusion et garde la trace (étape 8). Pas non plus de
    `canPublishData` : la discussion est envoyée au backend, qui la conserve puis la diffuse ;
    personne ne peut donc se faire passer pour un autre dans le chat (étape 11).
    """
    del role  # les droits ne dépendent pas du rôle : voir ci-dessus
    return {
        "room": room,
        "roomJoin": True,
        "canSubscribe": True,
        "canPublish": True,
        "canPublishData": False,
        "canPublishSources": list(PUBLISH_SOURCES),
    }


def livekit_expiry(now: datetime, closes_at: datetime) -> datetime:
    """Expiration d'un jeton : `LIVEKIT_TOKEN_TTL` après `now`, mais pas après la coupure."""
    return min(now + LIVEKIT_TOKEN_TTL, closes_at)


def issue_livekit_token(
    *,
    api_key: str,
    api_secret: str,
    room: str,
    identity: str,
    name: str,
    role: Role,
    external_id: str | None,
    issued_at: datetime,
    expires_at: datetime,
) -> str:
    """Jeton d'accès LiveKit. `metadata` (rôle, `external_id`) est relu par les webhooks LiveKit
    (présence, transcription) et par l'interface (badge organisateur)."""
    metadata = json.dumps(
        {"role": role.value, "external_id": external_id}, ensure_ascii=False, separators=(",", ":")
    )
    issued = int(issued_at.timestamp())
    claims: dict[str, Any] = {
        "iss": api_key,
        "sub": identity,
        "name": name,
        "metadata": metadata,
        "iat": issued,
        "nbf": issued,
        "exp": int(expires_at.timestamp()),
        "video": video_grant(role, room),
    }
    return jwt.encode(claims, api_secret, algorithm=ALGORITHM)


def client_ws_url(livekit_url: str) -> str:
    """URL à donner au navigateur : `http(s)://` devient `ws(s)://`."""
    for plain, websocket in (("https://", "wss://"), ("http://", "ws://")):
        if livekit_url.startswith(plain):
            return websocket + livekit_url.removeprefix(plain)
    return livekit_url
