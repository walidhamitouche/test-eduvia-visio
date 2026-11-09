"""Erreurs de l'API : toujours `{"error": {"code": "...", "message": "..."}}` (DECISIONS.md §3)."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

from visio.domain.errors import DomainError

logger = logging.getLogger("visio")

# Code d'erreur métier -> statut HTTP. Un code absent d'ici sort en 400.
DOMAIN_STATUS: dict[str, int] = {
    "invalid_slot": 422,
    "meeting_not_found": 404,
    "meeting_already_started": 409,
    "meeting_cancelled": 409,
    "meeting_not_started": 409,
    "meeting_ended": 409,
    "too_many_organizers": 409,
    "invalid_link": 404,
    "participant_excluded": 403,
    "not_open_yet": 409,
    "meeting_full": 409,
    "display_name_required": 422,
    "organizer_required": 403,
    "participant_not_found": 404,
    "target_is_organizer": 403,
    "not_admitted": 403,
    "media_server_unavailable": 502,
    "artifact_not_found": 404,
    "artifact_not_ready": 409,
    "artifact_deleted": 410,
    "artifact_file_missing": 404,
    "invalid_download_link": 404,
    "download_link_expired": 410,
    "storage_unavailable": 502,
    "range_not_satisfiable": 416,
}


class ApiError(Exception):
    """Erreur propre à la couche HTTP (authentification, HTTPS)."""

    def __init__(
        self, status_code: int, code: str, message: str, headers: dict[str, str] | None = None
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.headers = headers


class ErrorDetail(BaseModel):
    code: str
    message: str
    opens_at: str | None = None  # seulement pour `not_open_yet` (ISO 8601, heure de Paris)


class ErrorResponse(BaseModel):
    """Corps de toute réponse d'erreur."""

    error: ErrorDetail


def error_response(
    status_code: int,
    code: str,
    message: str,
    headers: dict[str, str] | None = None,
    extra: dict[str, Any] | None = None,
) -> JSONResponse:
    error: dict[str, Any] = {"code": code, "message": message}
    if extra:
        error |= extra
    return JSONResponse({"error": error}, status_code=status_code, headers=headers)


_VALIDATION_MESSAGES = {
    "missing": "champ obligatoire",
    "extra_forbidden": "champ inconnu",
    "json_invalid": "corps JSON invalide",
}


def _validation_message(errors: list[Any]) -> str:
    parts: list[str] = []
    for error in errors:
        location = ".".join(str(part) for part in error["loc"] if part != "body")
        text = _VALIDATION_MESSAGES.get(error["type"], str(error["msg"]))
        parts.append(f"{location} : {text}" if location else text)
    return "; ".join(parts) or "requête invalide"


_HTTP_CODES = {404: "not_found", 405: "method_not_allowed"}


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
        return error_response(exc.status_code, exc.code, exc.message, exc.headers)

    @app.exception_handler(DomainError)
    async def _domain_error(_: Request, exc: DomainError) -> JSONResponse:
        headers = None
        if exc.code == "range_not_satisfiable":  # RFC 9110 : la taille réelle accompagne le 416
            headers = {"Content-Range": f"bytes */{exc.details.get('size', 0)}"}
        return error_response(
            DOMAIN_STATUS.get(exc.code, 400), exc.code, exc.message, headers, extra=exc.details
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        return error_response(422, "validation_error", _validation_message(list(exc.errors())))

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _HTTP_CODES.get(exc.status_code, "http_error")
        message = "ressource introuvable" if exc.status_code == 404 else str(exc.detail)
        headers = dict(exc.headers) if exc.headers else None
        return error_response(exc.status_code, code, message, headers)

    @app.exception_handler(Exception)
    async def _unexpected(_: Request, exc: Exception) -> JSONResponse:
        logger.exception("erreur inattendue", exc_info=exc)
        return error_response(500, "internal_error", "erreur interne du serveur")
