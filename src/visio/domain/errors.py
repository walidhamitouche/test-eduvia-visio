"""Erreurs métier. Le `code` est celui renvoyé par l'API (`{"error": {"code": ...}}`)."""

from __future__ import annotations

from typing import Any


class DomainError(Exception):
    code = "domain_error"

    def __init__(self, message: str, **details: Any) -> None:
        super().__init__(message)
        self.message = message
        # Champs supplémentaires de la réponse d'erreur (ex. `opens_at`), JSON sérialisables.
        self.details: dict[str, Any] = details


class InvalidSlotError(DomainError):
    """Créneau incohérent (besoin §4) : renvoyé en `422 invalid_slot`."""

    code = "invalid_slot"


class MeetingNotFoundError(DomainError):
    """Aucune réunion avec cet identifiant : `404 meeting_not_found`."""

    code = "meeting_not_found"


class MeetingAlreadyStartedError(DomainError):
    """Modification ou annulation refusée, la réunion a commencé : `409 meeting_already_started`."""

    code = "meeting_already_started"


class MeetingCancelledError(DomainError):
    """Réunion annulée : plus aucune action possible (`409 meeting_cancelled`)."""

    code = "meeting_cancelled"


class MeetingNotStartedError(DomainError):
    """Clôture demandée avant l'ouverture : il faut annuler (`409 meeting_not_started`)."""

    code = "meeting_not_started"


class MeetingEndedError(DomainError):
    """Réunion terminée : plus aucun lien ne peut être créé (`409 meeting_ended`)."""

    code = "meeting_ended"


class TooManyOrganizersError(DomainError):
    """Plus de 10 liens organisateur actifs (DECISIONS.md point 12) : `409 too_many_organizers`."""

    code = "too_many_organizers"


class InvalidLinkError(DomainError):
    """Lien falsifié, inventé, modifié ou inconnu : `404 invalid_link`.

    Le message est volontairement unique : il ne dit jamais pourquoi le lien est refusé.
    """

    code = "invalid_link"


class LinkRevokedError(DomainError):
    """Lien révoqué (participant exclu) : `403 participant_excluded`."""

    code = "participant_excluded"


class MeetingFullError(DomainError):
    """Plafond de participants connectés atteint : `409 meeting_full`."""

    code = "meeting_full"


class NotOpenYetError(DomainError):
    """Lien valide mais réunion pas encore ouverte : `409 not_open_yet`, avec `opens_at`."""

    code = "not_open_yet"


class DisplayNameRequiredError(DomainError):
    """Lien générique : le participant doit saisir son nom (`422 display_name_required`)."""

    code = "display_name_required"


class OrganizerRequiredError(DomainError):
    """Action réservée aux organisateurs, demandée avec un lien participant : `403`."""

    code = "organizer_required"


class ParticipantNotFoundError(DomainError):
    """La personne visée n'est pas (ou plus) dans la réunion : `404 participant_not_found`."""

    code = "participant_not_found"


class TargetIsOrganizerError(DomainError):
    """Un organisateur ne peut pas en modérer un autre (ni lui-même) : `403`."""

    code = "target_is_organizer"


class NotAdmittedError(DomainError):
    """Personne pas (ou plus) admise dans la réunion (salle d'attente, exclusion) : `403`."""

    code = "not_admitted"


class MediaServerError(DomainError):
    """Le serveur de médias ne répond pas ou refuse l'ordre : `502 media_server_unavailable`."""

    code = "media_server_unavailable"


# --- artefacts : enregistrements, transcriptions (étape 15) ------------------------------------


class ArtifactNotFoundError(DomainError):
    """Aucun artefact de ce type avec cet identifiant : `404 artifact_not_found`."""

    code = "artifact_not_found"


class ArtifactNotReadyError(DomainError):
    """Artefact encore en cours de production (`pending`) : `409 artifact_not_ready`.

    On ne le supprime pas (Egress ou la fusion écrit encore dans le stockage) et on n'a rien à
    télécharger.
    """

    code = "artifact_not_ready"


class ArtifactDeletedError(DomainError):
    """URL de téléchargement valide mais artefact supprimé depuis : `410 artifact_deleted`."""

    code = "artifact_deleted"


class ArtifactFileMissingError(DomainError):
    """L'artefact existe en base mais son fichier n'est plus dans le stockage : `404`."""

    code = "artifact_file_missing"


class InvalidDownloadLinkError(DomainError):
    """URL de téléchargement falsifiée, inventée ou modifiée : `404 invalid_download_link`.

    Le message est volontairement unique : il ne dit jamais pourquoi l'URL est refusée.
    """

    code = "invalid_download_link"


class DownloadLinkExpiredError(DomainError):
    """URL de téléchargement authentique mais périmée (15 min au plus) : `410`."""

    code = "download_link_expired"


class StorageUnavailableError(DomainError):
    """Le stockage S3 ne répond pas ou refuse l'opération : `502 storage_unavailable`."""

    code = "storage_unavailable"


class RangeNotSatisfiableError(DomainError):
    """Plage d'octets demandée hors du fichier : `416 range_not_satisfiable` (avec `size`)."""

    code = "range_not_satisfiable"
