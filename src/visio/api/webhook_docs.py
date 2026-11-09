"""Webhooks sortants décrits dans l'OpenAPI (étape 21b), section `webhooks`.

Ce module ne sert **qu'à la documentation** : ces routes n'existent pas sur le serveur, ce sont les
requêtes que Visio envoie vers `WEBHOOK_URL`. Les formes ci-dessous doivent rester identiques à ce
que `services/webhooks.emit` écrit ; `tests/unit/test_webhook_docs.py` vérifie que chaque type
d'événement émis est documenté et que l'exemple de `docs/WEBHOOKS.md` est valide.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, FastAPI, Header
from pydantic import BaseModel, Field

router = APIRouter()


class WebhookMeeting(BaseModel):
    id: str = Field(description="Identifiant de la réunion (`mtg_…`).")
    title: str
    starts_at: datetime = Field(description="Début du créneau (heure de Paris).")
    ends_at: datetime = Field(description="Fin du créneau (heure de Paris).")
    metadata: dict[str, Any] = Field(
        description="Celui fourni à la création : c'est la clé de rapprochement côté Eduvia."
    )


class WebhookParticipant(BaseModel):
    display_name: str
    external_id: str | None = Field(description="Identifiant Eduvia, `null` sur un lien générique.")
    role: Literal["organizer", "participant"]


class _Event(BaseModel):
    id: str = Field(
        description="Identifiant de l'événement (`evt_…`), **identique à chaque tentative** : "
        "dédoublonner dessus."
    )
    created_at: datetime = Field(description="Heure de Paris, offset explicite.")
    meeting: WebhookMeeting


class MeetingStartedData(BaseModel):
    started_at: datetime


class MeetingStartedEvent(_Event):
    type: Literal["meeting.started"]
    data: MeetingStartedData


class ParticipantJoinedData(BaseModel):
    participant: WebhookParticipant
    joined_at: datetime


class ParticipantJoinedEvent(_Event):
    type: Literal["participant.joined"]
    data: ParticipantJoinedData


class ParticipantLeftData(BaseModel):
    participant: WebhookParticipant
    joined_at: datetime
    left_at: datetime
    duration_seconds: int


class ParticipantLeftEvent(_Event):
    type: Literal["participant.left"]
    data: ParticipantLeftData


class MeetingEndedData(BaseModel):
    reason: Literal["slot_end", "organizer", "api"]
    started_at: datetime | None
    ended_at: datetime


class MeetingEndedEvent(_Event):
    type: Literal["meeting.ended"]
    data: MeetingEndedData


class RecordingReadyData(BaseModel):
    recording_id: str = Field(description="À passer à `GET /v1/recordings/{recording_id}`.")
    size_bytes: int
    duration_seconds: int | None = Field(description="`null` si inconnue.")


class RecordingReadyEvent(_Event):
    type: Literal["recording.ready"]
    data: RecordingReadyData


class FailureData(BaseModel):
    reason: str = Field(description="Motif lisible (texte).")


class RecordingFailedData(FailureData):
    recording_id: str


class RecordingFailedEvent(_Event):
    type: Literal["recording.failed"]
    data: RecordingFailedData


class TranscriptReadyData(BaseModel):
    transcript_id: str
    language: str
    segments: int = Field(description="0 pour une réunion sans parole.")
    speakers: int
    duration_seconds: int | None


class TranscriptReadyEvent(_Event):
    type: Literal["transcript.ready"]
    data: TranscriptReadyData


class TranscriptFailedData(FailureData):
    transcript_id: str


class TranscriptFailedEvent(_Event):
    type: Literal["transcript.failed"]
    data: TranscriptFailedData


RECEIVED: dict[int | str, dict[str, Any]] = {
    200: {"description": "Reçu. **Tout code 2xx** vaut accusé ; tout autre échoue."}
}
SIGNATURE = (
    "En-têtes : `X-Visio-Event-Id`, `X-Visio-Timestamp`, `X-Visio-Signature` = `v1=` + "
    "HMAC-SHA256(`WEBHOOK_SECRET`, `<timestamp>.<corps brut>`) en hexadécimal. Refuser si le "
    "timestamp a plus de 5 minutes. Relances après 10 s, 1 min, 5 min, 30 min, 2 h. Voir "
    "`docs/WEBHOOKS.md`."
)


def _headers(
    event_id: str = Header(alias="X-Visio-Event-Id", description="Égal à `id` du corps."),
    timestamp: str = Header(alias="X-Visio-Timestamp", description="Secondes Unix."),
    signature: str = Header(alias="X-Visio-Signature", description="`v1=<hex>`."),
) -> None:
    """Dépendance de documentation : déclare les trois en-têtes de signature."""


def _doc(name: str, summary: str, when: str) -> Any:
    return router.post(
        name,
        summary=summary,
        description=f"{when}\n\n{SIGNATURE}",
        responses=RECEIVED,
        tags=["webhooks"],
    )


def _register() -> None:
    from fastapi import Depends

    cases: list[tuple[str, str, str, type[BaseModel]]] = [
        (
            "meeting.started",
            "Réunion démarrée",
            "La première personne admise est connectée.",
            MeetingStartedEvent,
        ),
        (
            "participant.joined",
            "Une personne entre",
            "Une personne entre en salle.",
            ParticipantJoinedEvent,
        ),
        (
            "participant.left",
            "Une personne sort",
            "Une personne sort, ou la salle est clôturée : ces événements partent avant "
            "`meeting.ended`.",
            ParticipantLeftEvent,
        ),
        (
            "meeting.ended",
            "Réunion terminée",
            "Fin de créneau, fin par l'organisateur ou par l'API.",
            MeetingEndedEvent,
        ),
        (
            "recording.ready",
            "Enregistrement prêt",
            "Le fichier est sur le S3, **après** `meeting.ended`. Ni clé S3 ni URL ici.",
            RecordingReadyEvent,
        ),
        (
            "recording.failed",
            "Enregistrement en échec",
            "Un seul de `recording.ready` / `recording.failed` par enregistrement.",
            RecordingFailedEvent,
        ),
        (
            "transcript.ready",
            "Transcription prête",
            "Le texte est sur le S3 : à lire par `GET /v1/meetings/{id}/transcript`.",
            TranscriptReadyEvent,
        ),
        (
            "transcript.failed",
            "Transcription en échec",
            "Après relances. Un rejeu réussi envoie ensuite `transcript.ready` pour le même id.",
            TranscriptFailedEvent,
        ),
    ]
    for name, summary, when, model in cases:

        def handler(body: Any, _headers_: None = Depends(_headers)) -> None:  # noqa: B008
            return None

        handler.__annotations__["body"] = model
        handler.__name__ = name.replace(".", "_")
        _doc(name, summary, when)(handler)


_register()


def install(app: FastAPI) -> None:
    """Retire de la section `webhooks` la réponse 422 que FastAPI ajoute à toute route à en-têtes :
    un webhook n'a pas d'erreur de validation, seulement 2xx (reçu) ou autre (échec)."""
    original = app.openapi

    def openapi() -> dict[str, Any]:
        schema = original()
        for item in schema.get("webhooks", {}).values():
            for operation in item.values():
                operation.get("responses", {}).pop("422", None)
        return schema

    app.openapi = openapi  # type: ignore[method-assign]
