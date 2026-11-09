"""Jetons des URL de téléchargement d'artefacts (besoin §5, étape 15).

Une URL `<domaine>/v1/downloads/<jeton>` donne accès à **un** artefact, pour **15 minutes au
plus** (« durée de vie courte : 15 min maximum »). Elle sert au backend d'Eduvia comme à un
navigateur : aucune clé d'API n'est nécessaire, c'est le jeton qui fait foi.

Principes :
- **Le jeton ne prouve que l'émission.** Il dit « nous avons émis cette URL pour cet artefact
  jusqu'à telle heure ». Que l'artefact existe encore, qu'il soit prêt et que son fichier soit
  sur le S3 se relit **à chaque requête** (`services.artifacts`) : c'est ce qui rend une URL déjà
  émise inutilisable dès la suppression.
- **Clé dédiée.** La clé de signature est dérivée de `LINK_SIGNING_SECRET` avec un contexte
  propre (HMAC-SHA256) et le jeton porte un usage (`use`) : un jeton de lien de visio n'ouvre
  aucun téléchargement, et inversement, même si les secrets sont les mêmes.
- **Horloge injectée.** L'expiration se compare à l'heure de l'application (`Clock`), pas à
  celle de PyJWT : les tests avancent l'horloge, et le comportement est identique en production.
- Mêmes garde-fous que les liens : HS256 seul, encodage canonique, taille plafonnée avant tout
  décodage, un seul message d'erreur pour tout jeton invalide.

Changer `LINK_SIGNING_SECRET` invalide aussi les URL de téléchargement en cours (15 min au plus).
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt

from visio.config import Settings
from visio.domain.errors import DownloadLinkExpiredError, InvalidDownloadLinkError
from visio.services.links import MAX_TOKEN_LENGTH, is_canonical_token

DOWNLOAD_URL_TTL = timedelta(minutes=15)
ALGORITHM = "HS256"
_PURPOSE = "artifact-download"
_KEY_CONTEXT = b"visio.download.v1"
_REQUIRED_CLAIMS = ("aid", "use", "exp")
_INVALID = "URL de téléchargement invalide"


@dataclass(frozen=True, slots=True)
class DownloadGrant:
    """Une URL de téléchargement émise : son jeton et l'instant où elle cesse de marcher."""

    token: str
    expires_at: datetime


def _signing_key(settings: Settings) -> bytes:
    secret = settings.link_signing_secret.get_secret_value().encode()
    return hmac.new(secret, _KEY_CONTEXT, hashlib.sha256).digest()


def issue(settings: Settings, artifact_id: str, *, now: datetime) -> DownloadGrant:
    """Émet un jeton pour `artifact_id`, valable 15 minutes au plus à partir de `now`."""
    # Arrondi vers le bas : la durée de vie n'est jamais plus longue que le plafond du besoin.
    expires = int((now + DOWNLOAD_URL_TTL).timestamp())
    claims: dict[str, Any] = {
        "aid": artifact_id,
        "use": _PURPOSE,
        "iat": int(now.timestamp()),
        "exp": expires,
    }
    token = jwt.encode(claims, _signing_key(settings), algorithm=ALGORITHM)
    return DownloadGrant(token=token, expires_at=datetime.fromtimestamp(expires, UTC))


def download_url(settings: Settings, grant: DownloadGrant) -> str:
    """URL publique, sur le domaine de l'API : le S3 n'est jamais exposé."""
    return f"{settings.public_base_url.rstrip('/')}/v1/downloads/{grant.token}"


def verify(settings: Settings, token: str, *, now: datetime) -> str:
    """Renvoie l'identifiant de l'artefact visé par un jeton authentique et non périmé.

    - jeton falsifié, inventé, mal formé ou d'un autre usage -> `InvalidDownloadLinkError` ;
    - jeton authentique mais périmé -> `DownloadLinkExpiredError` (seul un jeton dont la
      signature est bonne apprend qu'il est périmé).
    """
    if not token or len(token) > MAX_TOKEN_LENGTH or not token.isascii():
        raise InvalidDownloadLinkError(_INVALID)
    try:
        if not is_canonical_token(token):
            raise InvalidDownloadLinkError(_INVALID)
        claims: dict[str, Any] = jwt.decode(
            token,
            _signing_key(settings),
            algorithms=[ALGORITHM],
            options={"require": list(_REQUIRED_CLAIMS), "verify_exp": False, "verify_iat": False},
        )
    except (jwt.PyJWTError, ValueError):
        raise InvalidDownloadLinkError(_INVALID) from None
    artifact_id, purpose, expires = claims["aid"], claims["use"], claims["exp"]
    if (
        purpose != _PURPOSE
        or not isinstance(artifact_id, str)
        or isinstance(expires, bool)
        or not isinstance(expires, int)
    ):
        raise InvalidDownloadLinkError(_INVALID)
    if now.timestamp() >= expires:
        msg = "cette URL de téléchargement a expiré : en demander une nouvelle à l'API"
        raise DownloadLinkExpiredError(msg)
    return artifact_id
