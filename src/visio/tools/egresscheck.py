"""Vérifie LiveKit Egress de bout en bout avec la vraie configuration (étape 13c).

    uv run task egress-check

Enregistre quelques secondes d'une page web (la page d'accueil de LiveKit lui-même, il n'y a donc
besoin d'aucun participant), attend la fin du traitement, vérifie que le fichier MP4 est arrivé
sur le S3 **que le backend lit**, puis le supprime. Une réussite prouve la chaîne complète :
backend -> LiveKit -> Redis -> Egress -> S3.

Lancé par `dotenvx run -f .env.development`, donc avec la configuration de l'API.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable

from visio.config import ConfigurationError, get_settings
from visio.domain.errors import MediaServerError
from visio.services.egress import EgressGateway, EgressInfo, EgressUnavailableError
from visio.services.storage import Storage, StorageError

# Vu depuis le conteneur Egress : il est dans le réseau Docker du compose et joint LiveKit par
# le nom de son service (voir docker-compose.dev.yml).
PAGE_TO_RECORD = "http://livekit:7880"
RECORD_SECONDS = 5.0
START_TIMEOUT = 90.0  # Egress met quelques secondes à s'enregistrer dans Redis après `up`
FINISH_TIMEOUT = 60.0
POLL = 1.0


def run_check(
    egress: EgressGateway,
    storage: Storage,
    *,
    page: str = PAGE_TO_RECORD,
    record_seconds: float = RECORD_SECONDS,
    start_timeout: float = START_TIMEOUT,
    finish_timeout: float = FINISH_TIMEOUT,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], float] = time.monotonic,
    log: Callable[[str], None] = print,
) -> list[str]:
    """Retourne la liste des problèmes (vide = tout est bon). Supprime son objet de test."""
    key = f"healthcheck/egress-{uuid.uuid4().hex}.mp4"
    problems: list[str] = []
    egress_id: str | None = None
    try:
        # 1. Démarrage : on réessaie tant qu'aucun service Egress ne répond.
        deadline = now() + start_timeout
        while True:
            try:
                started = egress.start_web_to_file(page, key)
                break
            except EgressUnavailableError as error:
                if now() >= deadline:
                    return [f"Egress ne répond pas après {start_timeout:.0f} s : {error}"]
                sleep(POLL)
        egress_id = started.egress_id
        log(f"  Egress {egress_id} : enregistrement démarré ({started.status})")

        # 2. L'enregistrement tourne quelques secondes.
        info = _wait_for(egress, egress_id, {"active"}, now() + finish_timeout, sleep, now)
        if info.status == "active":
            sleep(record_seconds)
            egress.stop(egress_id)
        # 3. Fin du traitement.
        info = _wait_for(egress, egress_id, None, now() + finish_timeout, sleep, now)
        if info.status != "complete":
            reason = f" : {info.error}" if info.error else ""
            return [f"l'enregistrement s'est terminé en « {info.status} »{reason}"]
        log("  OK : enregistrement terminé (complete)")

        # 4. Le fichier est sur le S3 du backend, non vide, et c'est bien un MP4.
        if not storage.exists(key):
            problems.append(
                f"le fichier {key} n'est pas arrivé sur le S3 (bucket {storage.bucket})"
            )
        else:
            content = storage.get(key)
            if len(content) < 1024:
                problems.append(f"fichier suspect : {len(content)} octets seulement")
            elif content[4:8] != b"ftyp":
                problems.append("le fichier produit n'est pas un MP4 (signature ftyp absente)")
            else:
                log(f"  OK : MP4 présent sur le S3 ({len(content)} octets)")
    except (MediaServerError, StorageError) as error:
        problems.append(f"échec : {error}")
    finally:
        _cleanup(egress, storage, egress_id, key, problems)
    return problems


def _wait_for(
    egress: EgressGateway,
    egress_id: str,
    wanted: set[str] | None,
    deadline: float,
    sleep: Callable[[float], None],
    now: Callable[[], float],
) -> EgressInfo:
    """Attend un statut de `wanted` ou un statut final (un final seulement si `wanted` est None)."""
    info = EgressInfo(egress_id, "starting", "", ())
    while now() < deadline:
        found = egress.list(egress_id)
        if found:
            info = found[0]
            if info.is_final or (wanted is not None and info.status in wanted):
                return info
        sleep(POLL)
    return info


def _cleanup(
    egress: EgressGateway,
    storage: Storage,
    egress_id: str | None,
    key: str,
    problems: list[str],
) -> None:
    """Ne laisse ni enregistrement en cours ni objet de test, quoi qu'il soit arrivé."""
    if egress_id is not None:
        try:
            found = egress.list(egress_id)
            if found and not found[0].is_final:
                egress.stop(egress_id)
        except MediaServerError:
            pass
    try:
        storage.delete(key)
        if storage.exists(key):
            problems.append("suppression : l'objet de test existe encore")
    except StorageError as error:
        problems.append(f"suppression impossible : {error}")


def main(argv: list[str] | None = None) -> int:
    del argv
    try:
        settings = get_settings()
    except ConfigurationError as error:
        print(f"Configuration invalide : {error}")
        return 1
    storage = Storage.from_settings(settings)
    egress = EgressGateway(
        settings.livekit_url,
        settings.livekit_api_key,
        settings.livekit_api_secret.get_secret_value(),
    )
    print(f"LiveKit : {settings.livekit_url}   S3 : {settings.s3_endpoint}/{settings.s3_bucket}")
    if not storage.bucket_exists():
        print("Le bucket n'existe pas : lance d'abord  uv run task s3-init")
        return 1
    problems = run_check(egress, storage)
    if problems:
        for problem in problems:
            print(f"  ECHEC : {problem}")
        print("Pistes : .\\deploy\\dev\\docker-dev.ps1 logs egress")
        return 1
    print("  OK : objet de test supprimé")
    print("Egress OK.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
