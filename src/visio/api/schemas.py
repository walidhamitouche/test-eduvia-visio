"""Formats d'entrée et de sortie de l'API réunions (besoin §4).

Entrées : types stricts (`"10"` n'est pas un entier, `1` n'est pas un booléen) et champs
inconnus refusés : une faute de frappe (`recordng`) ne doit jamais donner une réunion sans
enregistrement en silence. Les dates restent des chaînes à l'entrée pour que la règle du
créneau les lise elle-même (décalage obligatoire, code `invalid_slot`).
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from visio.db.models import ArtifactStatus
from visio.domain.meeting import MeetingState, SlotStatus
from visio.domain.roles import Role
from visio.domain.slot import MAX_EARLY_JOIN_MINUTES

MAX_ORGANIZERS = 10
MAX_METADATA_BYTES = 16 * 1024

Text200 = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
IsoDate = Annotated[
    str,
    Field(
        description="Date ISO 8601 **avec décalage horaire** (`+01:00`, `Z`). Sans : refusée.",
        examples=["2027-01-02T16:00:00+01:00"],
        json_schema_extra={"format": "date-time"},
    ),
]


class _StrictIn(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")


def _check_metadata(value: dict[str, Any]) -> dict[str, Any]:
    try:
        size = len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode())
    except (TypeError, ValueError):
        msg = "metadata doit être du JSON valide (NaN et Infinity sont interdits)"
        raise ValueError(msg) from None
    if size > MAX_METADATA_BYTES:
        msg = f"metadata est limité à {MAX_METADATA_BYTES // 1024} Ko"
        raise ValueError(msg)
    return value


class OrganizerIn(_StrictIn):
    display_name: Text200 = Field(description="Nom affiché, non modifiable dans la visio.")
    external_id: Text200 | None = Field(
        default=None, description="Identifiant de la personne chez Eduvia (ex. `staff_42`)."
    )


class CreateMeetingIn(_StrictIn):
    model_config = ConfigDict(
        strict=True,
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "title": "Cours BTS MCO – Groupe A",
                    "starts_at": "2027-01-02T16:00:00+01:00",
                    "ends_at": "2027-01-02T18:00:00+01:00",
                    "early_join_minutes": 10,
                    "waiting_room": True,
                    "recording": True,
                    "transcription": True,
                    "organizers": [{"display_name": "Marie Durand", "external_id": "staff_42"}],
                    "metadata": {"eduvia_session_id": 1234},
                }
            ]
        },
    )

    title: Text200
    starts_at: IsoDate
    ends_at: IsoDate
    early_join_minutes: int = Field(
        default=0,
        description=f"Minutes d'entrée anticipée avant `starts_at` (0 à {MAX_EARLY_JOIN_MINUTES}).",
    )
    waiting_room: bool = Field(default=False, description="Admission par l'organisateur.")
    recording: bool = Field(default=False, description="Enregistrement audio + vidéo + partage.")
    transcription: bool = Field(default=False, description="Transcription après la réunion.")
    organizers: list[OrganizerIn] = Field(
        min_length=1,
        max_length=MAX_ORGANIZERS,
        description="Au moins un organisateur ; un lien organisateur est généré pour chacun.",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description=f"Objet JSON libre (max {MAX_METADATA_BYTES // 1024} Ko), renvoyé tel quel.",
    )

    _metadata_ok = field_validator("metadata")(_check_metadata)


class UpdateMeetingIn(_StrictIn):
    """Seuls les champs envoyés sont modifiés. `metadata`, s'il est envoyé, remplace l'ancien
    objet en entier. Les organisateurs ne se modifient pas ici (liens : étape 5)."""

    title: Text200 | None = None
    starts_at: IsoDate | None = None
    ends_at: IsoDate | None = None
    early_join_minutes: int | None = None
    waiting_room: bool | None = None
    recording: bool | None = None
    transcription: bool | None = None
    metadata: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _no_explicit_null(self) -> UpdateMeetingIn:
        for name in self.model_fields_set:
            if getattr(self, name) is None:
                msg = "ne peut pas être null (omettre le champ pour ne pas le modifier)"
                raise ValueError(f"{name} {msg}")
        return self

    @field_validator("metadata")
    @classmethod
    def _metadata_ok(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        return None if value is None else _check_metadata(value)


class ConnectedParticipantOut(BaseModel):
    display_name: str
    external_id: str | None
    role: Role
    joined_at: datetime


class MeetingOut(BaseModel):
    id: str
    title: str
    status: MeetingState = Field(
        description="`scheduled` (planifiée), `live` (en cours), `ended` (terminée), "
        "`cancelled` (annulée). Dates en heure de Paris, avec décalage."
    )
    starts_at: datetime
    ends_at: datetime
    early_join_minutes: int
    waiting_room: bool
    recording: bool
    transcription: bool
    started_at: datetime | None = Field(description="Première connexion (`null` avant).")
    ended_at: datetime | None = Field(description="Clôture anticipée (`null` si fin normale).")
    metadata: dict[str, Any]
    participants: list[ConnectedParticipantOut] = Field(description="Personnes connectées.")
    created_at: datetime
    updated_at: datetime


class OrganizerLinkOut(BaseModel):
    display_name: str
    external_id: str | None
    url: str = Field(description="Lien secret de cet organisateur.")


class MeetingCreatedOut(MeetingOut):
    participant_url: str = Field(description="Lien participant générique (partageable).")
    organizer_urls: list[str] = Field(
        description="Liens organisateurs, **dans l'ordre** des `organizers` de la requête."
    )
    organizers: list[OrganizerLinkOut] = Field(description="Les mêmes liens, avec le nom associé.")


class CreateLinkIn(_StrictIn):
    """Lien nominatif : le nom est pré-rempli dans la visio et ne se modifie pas."""

    model_config = ConfigDict(
        strict=True,
        extra="forbid",
        json_schema_extra={
            "examples": [
                {"role": "participant", "display_name": "Lina Benali", "external_id": "stu_981"}
            ]
        },
    )

    role: Literal["participant", "organizer"] = Field(
        description="`participant` ou `organizer` (droits de modération)."
    )
    display_name: Text200 = Field(description="Nom affiché, non modifiable dans la visio.")
    external_id: Text200 | None = Field(
        default=None, description="Identifiant de la personne chez Eduvia (ex. `stu_981`)."
    )


class LinkOut(BaseModel):
    id: str = Field(description="Identifiant du lien (`jti`).")
    role: Role
    display_name: str | None
    external_id: str | None
    url: str = Field(description="URL secrète du lien. Renvoyée **uniquement** ici, jamais relue.")
    created_at: datetime


# --- rejoindre une réunion (étape 6) ---------------------------------------------------------

ClientId = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{16,64}$")]


class JoinIn(_StrictIn):
    """Ce que l'interface de visio envoie quand quelqu'un ouvre un lien."""

    token: str = Field(description="Le jeton de l'URL `<domaine>/j/<jeton>`.")
    display_name: Text200 | None = Field(
        default=None,
        description="Nom saisi à l'entrée. **Obligatoire** sur le lien générique ; ignoré sur un "
        "lien nominatif (le nom est celui du lien et ne se modifie pas).",
    )
    client_id: ClientId | None = Field(
        default=None,
        description="Identifiant propre à ce navigateur (16 à 64 caractères `A-Za-z0-9_-`), "
        "mémorisé par l'interface. Sur le lien générique, il distingue deux personnes qui "
        "partagent le lien. Absent à la première visite : le serveur en fournit un.",
    )


class JoinMeetingOut(BaseModel):
    title: str
    starts_at: datetime
    ends_at: datetime
    waiting_room: bool
    recording: bool = Field(description="Vrai : l'interface doit afficher l'indicateur.")
    transcription: bool


class JoinParticipantOut(BaseModel):
    display_name: str
    external_id: str | None
    role: Role


class JoinOut(BaseModel):
    status: Literal["admitted", "waiting", "denied"] = Field(
        description="`admitted` : jeton émis. `waiting` : salle d'attente, **aucun jeton** : "
        "rappeler cette route toutes les 2 s. `denied` : refusé par un organisateur."
    )
    livekit_url: str | None = Field(
        description="URL WebSocket du serveur de médias pour le navigateur (`null` sans jeton)."
    )
    livekit_token: str | None = Field(
        description="Jeton de salle de courte durée. Une reconnexion après `expires_at` repasse "
        "par cette route. `null` tant que la personne n'est pas admise."
    )
    expires_at: datetime | None = Field(description="Expiration du jeton (Paris).")
    seconds_until_end: int = Field(description="Secondes avant la coupure de la réunion.")
    client_id: str | None = Field(
        description="À mémoriser dans le navigateur et renvoyer à la prochaine entrée. "
        "`null` sur un lien nominatif."
    )
    meeting: JoinMeetingOut
    participant: JoinParticipantOut


class PreviewIn(_StrictIn):
    """Aperçu d'un lien : ne donne aucun accès, ne crée rien."""

    token: str = Field(description="Le jeton de l'URL `<domaine>/j/<jeton>`.")


class PreviewParticipantOut(BaseModel):
    role: Role
    display_name: str | None = Field(
        description="Nom du lien nominatif (affiché et verrouillé) ; `null` sur le lien générique."
    )
    external_id: str | None
    name_required: bool = Field(description="Vrai sur le lien générique : le nom est à saisir.")


class PreviewOut(BaseModel):
    status: SlotStatus = Field(
        description="`not_open_yet`, `open`, `ended` ou `cancelled` (règle du créneau)."
    )
    server_time: datetime = Field(
        description="Heure du serveur (Paris) : l'interface s'en sert pour son compte à rebours, "
        "sans se fier à l'horloge de l'appareil."
    )
    opens_at: datetime = Field(description="Première heure d'entrée possible (Paris).")
    meeting: JoinMeetingOut
    participant: PreviewParticipantOut


# --- Routes de la salle (authentifiées par le lien, étapes 8, 9 et 11) -------------------------


class LinkIn(_StrictIn):
    token: str = Field(description="Le jeton du lien de l'appelant (`<domaine>/j/<jeton>`).")


class MuteIn(LinkIn):
    identity: str = Field(description="Identité de la personne visée (celle de la salle).")
    source: Literal["microphone", "camera", "screen_share"] = Field(
        description="Ce qu'il faut couper : micro, caméra ou partage d'écran."
    )


class RemoveIn(LinkIn):
    identity: str = Field(description="Identité de la personne à exclure définitivement.")


class MuteOut(BaseModel):
    muted: int = Field(description="Nombre de pistes coupées (0 si la personne ne publie rien).")


class OkOut(BaseModel):
    ok: bool = True


class LobbyEntryOut(BaseModel):
    id: str = Field(description="Identifiant de la demande (à passer à `admit` ou `deny`).")
    display_name: str
    external_id: str | None
    status: Literal["waiting", "denied"]
    requested_at: datetime


class LobbyOut(BaseModel):
    entries: list[LobbyEntryOut]


class LobbyDecisionIn(LinkIn):
    ids: list[str] = Field(min_length=1, max_length=100, description="Demandes concernées.")


class LobbyCountOut(BaseModel):
    count: int = Field(description="Nombre de personnes concernées par la décision.")


class ChatIn(LinkIn):
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
    client_id: str | None = Field(
        default=None, description="Celui renvoyé par `/v1/join` (lien générique seulement)."
    )


class ChatAuthorOut(BaseModel):
    display_name: str
    external_id: str | None
    role: Role


class ChatMessageOut(BaseModel):
    id: str
    author: ChatAuthorOut
    sent_at: datetime
    text: str


class ChatExportOut(BaseModel):
    messages: list[ChatMessageOut]


class AttendanceSessionOut(BaseModel):
    joined_at: datetime
    left_at: datetime | None = Field(description="`null` tant que la personne est connectée.")
    seconds: int = Field(description="Durée de cette connexion (jusqu'à maintenant si ouverte).")


class AttendanceParticipantOut(BaseModel):
    display_name: str
    external_id: str | None
    role: Role
    total_seconds: int = Field(
        description="Temps de présence : durée de l'**union** des connexions (sans double "
        "comptage), jusqu'à la fin de la réunion au plus."
    )
    first_joined_at: datetime
    last_left_at: datetime | None
    sessions: list[AttendanceSessionOut]


class AttendanceOut(BaseModel):
    meeting_id: str
    participants: list[AttendanceParticipantOut]


class RecordingOut(BaseModel):
    """Un enregistrement et, s'il est prêt, son URL de téléchargement temporaire."""

    id: str
    meeting_id: str
    status: ArtifactStatus = Field(
        description="`pending` : en cours d'enregistrement ou de traitement ; `ready` : "
        "téléchargeable ; `failed` : échec, voir `error`."
    )
    size_bytes: int | None = Field(description="Taille du fichier, quand il est prêt.")
    error: str | None = Field(description="Motif lisible, seulement si `status` vaut `failed`.")
    created_at: datetime
    download_url: str | None = Field(
        description="URL de téléchargement, seulement si `ready`. **Valable 15 minutes au plus**, "
        "sans clé d'API (l'URL fait foi) ; en redemander une à chaque besoin. Elle cesse de "
        "fonctionner dès la suppression de l'enregistrement."
    )
    download_url_expires_at: datetime | None = Field(
        description="Instant (heure de Paris) où `download_url` cesse de fonctionner."
    )


class RecordingsOut(BaseModel):
    meeting_id: str
    recordings: list[RecordingOut]


class TranscriptSegmentOut(BaseModel):
    start: float = Field(description="Début, en secondes depuis le début de l'enregistrement.")
    end: float = Field(description="Fin, en secondes depuis le début de l'enregistrement.")
    speaker: str = Field(description="Nom affiché de la personne (celui de son lien).")
    external_id: str | None = Field(description="Identifiant chez Eduvia, s'il y en a un.")
    text: str


class TranscriptSpeakerOut(BaseModel):
    display_name: str
    external_id: str | None


class TranscriptOut(BaseModel):
    """La transcription d'une réunion (JSON par segments, horodaté et par locuteur)."""

    id: str
    meeting_id: str
    status: ArtifactStatus = Field(
        description="`pending` : en cours de production ; `ready` : disponible ; "
        "`failed` : échec, voir `error`. Seul `ready` porte des segments."
    )
    error: str | None = Field(description="Motif lisible, seulement si `status` vaut `failed`.")
    created_at: datetime
    language: str | None = Field(description="Langue transcrite (`fr`), quand elle est prête.")
    started_at: datetime | None = Field(
        description="Instant (heure de Paris) du début de l'enregistrement : `start` = 0 s."
    )
    duration_seconds: float | None
    speakers: list[TranscriptSpeakerOut]
    segments: list[TranscriptSegmentOut]


# --- santé détaillée (étape 17b) ------------------------------------------------------------------


class DependencyHealthOut(BaseModel):
    status: Literal["ok", "down"]
    latency_ms: float = Field(description="Durée de la sonde, en millisecondes.")
    detail: str | None = Field(
        default=None,
        description="Texte fixe (`injoignable`, `délai dépassé`) quand `status` vaut `down`. "
        "Jamais d'adresse, de clé ni de message d'erreur.",
    )


class JobsQueueOut(BaseModel):
    pending: int = Field(description="Tâches en attente (heure passée ou non).")
    failed: int = Field(description="Tâches dont un essai a échoué ; le suivant est programmé.")
    abandoned: int = Field(description="Tâches abandonnées après tous leurs essais.")
    oldest_due_age_seconds: int | None = Field(
        description="Âge de la plus ancienne tâche due non prise en charge (`null` si aucune)."
    )
    late: bool = Field(description="Une tâche due attend depuis trop longtemps (selon son type).")


class WebhooksQueueOut(BaseModel):
    pending: int = Field(description="Événements à livrer.")
    abandoned: int = Field(description="Événements abandonnés après tous leurs essais.")
    oldest_due_age_seconds: int | None = Field(
        description="Âge du plus ancien événement dont l'envoi est dû (`null` si aucun)."
    )
    late: bool


class QueueHealthOut(BaseModel):
    status: Literal["ok", "degraded"] = Field(
        description="`degraded` si une tâche ou un webhook est abandonné, "
        "ou si la file est en retard."
    )
    jobs: JobsQueueOut
    webhooks: WebhooksQueueOut
    artifacts_failed: int = Field(
        description="Enregistrements et transcriptions en échec "
        "(information, sans effet sur le statut)."
    )


class HealthChecksOut(BaseModel):
    database: DependencyHealthOut
    livekit: DependencyHealthOut
    storage: DependencyHealthOut


class HealthDetailOut(BaseModel):
    status: Literal["ok", "degraded", "down"] = Field(
        description="`ok` (HTTP 200), `degraded` : une dépendance ou la file souffre (HTTP 503), "
        "`down` : la base est injoignable (HTTP 503)."
    )
    version: str
    checked_at: datetime
    checks: HealthChecksOut
    queue: QueueHealthOut | None = Field(
        description="`null` quand la base est injoignable : l'état de la file est illisible."
    )
