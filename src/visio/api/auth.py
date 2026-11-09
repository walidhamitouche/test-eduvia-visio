"""Authentification de l'API par clé secrète (besoin §6 : « aucune route de création sans
authentification »). Seul le backend Eduvia connaît cette clé."""

from __future__ import annotations

import hashlib
import hmac
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from visio.api.errors import ApiError
from visio.config import Settings

_bearer = HTTPBearer(
    auto_error=False,  # le 401 est renvoyé par nous, au format d'erreur de l'API
    scheme_name="ApiKey",
    description="Clé secrète de l'API (`Authorization: Bearer <clé>`), connue du backend Eduvia.",
)


def _is_https(request: Request) -> bool:
    # Derrière Coolify / un proxy, la connexion interne est en http : le proxy indique le
    # protocole d'origine dans X-Forwarded-Proto.
    forwarded = request.headers.get("x-forwarded-proto")
    scheme = forwarded.split(",")[0].strip().lower() if forwarded else request.url.scheme
    return scheme == "https"


def _same_secret(provided: str, expected: str) -> bool:
    """Comparaison à temps constant (sur des empreintes de même longueur)."""
    return hmac.compare_digest(
        hashlib.sha256(provided.encode()).digest(), hashlib.sha256(expected.encode()).digest()
    )


def require_https(request: Request) -> None:
    """En production, HTTP simple est refusé (les clés et les liens ne doivent pas circuler en
    clair). Utilisé aussi par la route publique `POST /v1/join`."""
    settings: Settings = request.app.state.settings
    if settings.app_env == "production" and not _is_https(request):
        raise ApiError(403, "https_required", "HTTPS est obligatoire pour appeler cette API")


def require_api_key(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> None:
    require_https(request)
    settings: Settings = request.app.state.settings
    if credentials is None or not _same_secret(
        credentials.credentials, settings.api_key.get_secret_value()
    ):
        raise ApiError(
            401,
            "unauthorized",
            "clé d'API absente ou invalide (en-tête Authorization: Bearer <clé>)",
            headers={"WWW-Authenticate": "Bearer"},
        )
