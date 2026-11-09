"""Middleware HTTP d'observabilité (étape 17a) : identifiant de requête et journal d'accès.

- `X-Request-ID` : repris de l'appelant s'il est sain (8 à 64 caractères `A-Za-z0-9._-`), sinon
  généré ; renvoyé dans la réponse et joint à tous les logs de la requête (`request_id`).
- `meeting_id` : lu dans l'URL `/v1/meetings/<id>/...` et joint à tous les logs de la requête.
- Une ligne d'accès par requête (`visio.access`) : méthode, **route générique**, statut, durée.
  Jamais l'URL brute ni la query string : les routes `/j/{token}` et `/v1/downloads/{token}`
  contiennent un jeton, et une URL inventée (scénario 2) peut contenir n'importe quoi.
- Middleware ASGI pur (pas `BaseHTTPMiddleware`) : le contexte de logs suit la requête jusque dans
  les fonctions synchrones exécutées dans un fil.
"""

from __future__ import annotations

import logging
import re
import secrets
import time
from typing import Any

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from visio import logging_setup

access_logger = logging.getLogger("visio.access")

REQUEST_ID_HEADER = "x-request-id"
_REQUEST_ID = re.compile(r"[A-Za-z0-9._-]{8,64}")
_MEETING_IN_PATH = re.compile(r"^/v1/meetings/(mtg_[0-9a-f]{32})(?:/|$)")
UNKNOWN_ROUTE = "(route inconnue)"
HEALTH_DETAIL_ROUTE = "/v1/health"
# Routes trop bavardes pour le niveau INFO (sondes de santé, fichiers de l'interface).
_QUIET_PREFIXES = ("/health", "/v1/health", "/assets/")


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        inbound = ""
        for name, value in scope["headers"]:
            if name == REQUEST_ID_HEADER.encode():
                inbound = value.decode("latin-1")
                break
        request_id = inbound if _REQUEST_ID.fullmatch(inbound) else "req_" + secrets.token_hex(8)
        found = _MEETING_IN_PATH.match(scope["path"])
        token = logging_setup.bind(
            request_id=request_id, meeting_id=found.group(1) if found else None
        )

        status = 500
        started = time.perf_counter()

        async def send_with_id(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = int(message["status"])
                headers = [
                    (k, v) for k, v in message.get("headers", []) if k.lower() != b"x-request-id"
                ]
                headers.append((b"x-request-id", request_id.encode()))
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        finally:
            self._log(scope, status, (time.perf_counter() - started) * 1000)
            logging_setup.unbind(token)

    @staticmethod
    def _log(scope: Scope, status: int, duration_ms: float) -> None:
        route: Any = scope.get("route")
        template = getattr(route, "path", None)
        fields: dict[str, Any] = {
            "method": scope["method"],
            "route": template or UNKNOWN_ROUTE,
            "status": status,
            "duration_ms": round(duration_ms, 1),
        }
        # Réunion connue seulement après le routage (ex. `/v1/room/...` n'en a pas dans l'URL).
        meeting = (scope.get("path_params") or {}).get("meeting_id")
        if meeting:
            fields["meeting_id"] = meeting
        if status == 503 and template == HEALTH_DETAIL_ROUTE:
            level = logging.WARNING  # « pas en forme » est le message de cette route, pas un bug
        elif status >= 500:
            level = logging.ERROR
        elif template and template.startswith(_QUIET_PREFIXES):
            level = logging.DEBUG
        else:
            level = logging.INFO
        access_logger.log(level, "requête", extra=fields)
