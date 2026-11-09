"""Vérifie de bout en bout l'enregistrement d'une réunion (étape 14b), en une commande.

    uv run task recording-check                       # la dernière réunion créée
    uv run task recording-check mtg_777f5e44...       # une réunion précise
    uv run task recording-check --wait 90             # attend jusqu'à 90 s que l'issue arrive
    uv run task recording-check --expect failed       # on s'attend à un échec (ex. sans piste)
    uv run task recording-check --probe               # télécharge le MP4 et l'examine (ffprobe)

Lecture seule (sauf `--probe`, qui écrit dans `data/recordings/`). Affiche `OK`, `ECHEC` ou `INFO`
pour chaque contrôle et rend le code 1 s'il y a au moins un `ECHEC`.

Contrôles : réunion terminée ; un seul artefact d'enregistrement ; issue tranchée (`ready` ou
`failed`) ; taille en base = taille sur le S3 ; un seul webhook `recording.*`, cohérent avec
l'état, **livré** (le récepteur a répondu 2xx, donc la signature était valide), avec les
`metadata`, sans clé S3 ni URL, et après `meeting.ended`. Une réunion sans `recording` ne doit
produire ni artefact ni événement.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from visio.config import ConfigurationError, get_settings
from visio.db import create_db_engine, create_session_factory
from visio.db.models import (
    ArtifactKind,
    ArtifactRow,
    ArtifactStatus,
    MeetingRow,
    WebhookDeliveryRow,
    WebhookStatus,
)
from visio.domain.meeting import MeetingState
from visio.services import webhooks
from visio.services.storage import ObjectSizer, Storage, StorageError

OK, FAIL, INFO = "OK", "ECHEC", "INFO"
DESTINATION = Path("data") / "recordings"
DURATION_TOLERANCE_SECONDS = 5

Line = tuple[str, str]


@dataclass
class Report:
    lines: list[Line]
    pending: bool = False  # l'issue n'est pas encore tranchée : cela vaut la peine d'attendre
    artifact: ArtifactRow | None = None

    @property
    def failed(self) -> bool:
        return any(level == FAIL for level, _ in self.lines)


def latest_meeting_id(db: Session) -> str | None:
    return db.scalar(select(MeetingRow.id).order_by(MeetingRow.created_at.desc()).limit(1))


def _recording_events(db: Session, meeting_id: str) -> list[WebhookDeliveryRow]:
    return list(
        db.scalars(
            select(WebhookDeliveryRow)
            .where(
                WebhookDeliveryRow.meeting_id == meeting_id,
                WebhookDeliveryRow.event_type.in_(
                    [webhooks.RECORDING_READY, webhooks.RECORDING_FAILED]
                ),
            )
            .order_by(WebhookDeliveryRow.created_at)
        ).all()
    )


def evaluate(
    db: Session, storage: ObjectSizer, meeting_id: str, *, expect: str = "ready"
) -> Report:
    """Les contrôles. `expect` : issue attendue (`ready` ou `failed`) quand l'option est active."""
    db.expire_all()
    lines: list[Line] = []
    meeting = db.get(MeetingRow, meeting_id)
    if meeting is None:
        return Report([(FAIL, f"réunion inconnue : {meeting_id}")])
    lines.append(
        (INFO, f"réunion {meeting.id} · état {meeting.state.value} · recording={meeting.recording}")
    )

    artifacts = list(
        db.scalars(
            select(ArtifactRow).where(
                ArtifactRow.meeting_id == meeting.id, ArtifactRow.kind == ArtifactKind.RECORDING
            )
        ).all()
    )
    events = _recording_events(db, meeting.id)

    if not meeting.recording:
        ok = not artifacts and not events
        lines.append(
            (OK if ok else FAIL, "sans `recording` : aucun artefact et aucun événement recording.*")
        )
        return Report(lines)

    if meeting.state is not MeetingState.ENDED:
        lines.append((FAIL, f"la réunion n'est pas terminée (état : {meeting.state.value})"))
        return Report(lines, pending=True)
    lines.append((OK, "réunion terminée"))

    if len(artifacts) != 1:
        lines.append((FAIL, f"{len(artifacts)} artefact(s) d'enregistrement (attendu : 1)"))
        return Report(lines)
    artifact = artifacts[0]
    lines.append((OK, f"un seul artefact d'enregistrement ({artifact.id})"))

    if artifact.status is ArtifactStatus.PENDING:
        lines.append(
            (FAIL, "l'issue n'est pas encore tranchée (artefact `pending`) : Egress n'a pas fini ?")
        )
        return Report(lines, pending=True, artifact=artifact)

    wanted = ArtifactStatus(expect)
    lines.append(
        (
            OK if artifact.status is wanted else FAIL,
            f"issue : {artifact.status.value} (attendu : {wanted.value})",
        )
    )
    expected_event = (
        webhooks.RECORDING_READY
        if artifact.status is ArtifactStatus.READY
        else webhooks.RECORDING_FAILED
    )
    if artifact.status is ArtifactStatus.READY:
        lines += _check_ready(artifact, storage)
    else:
        lines.append((INFO if artifact.error else FAIL, f"motif : {artifact.error or 'AUCUN'}"))
    lines += _check_webhook(db, meeting, artifact, events, expected_event)
    # Événement écrit mais pas encore tenté : le planificateur passe chaque seconde, on attend.
    waiting = (
        len(events) == 1 and not events[0].attempts and events[0].status is WebhookStatus.PENDING
    )
    return Report(lines, pending=waiting, artifact=artifact)


def _check_ready(artifact: ArtifactRow, storage: ObjectSizer) -> list[Line]:
    lines: list[Line] = []
    if not artifact.size_bytes:
        lines.append((FAIL, "taille en base absente ou nulle"))
        return lines
    assert artifact.storage_key is not None
    try:
        on_s3 = storage.size(artifact.storage_key)
    except StorageError as error:
        return [(FAIL, f"S3 illisible : {error}")]
    if on_s3 is None:
        lines.append((FAIL, f"fichier absent du S3 : {artifact.storage_key}"))
    elif on_s3 != artifact.size_bytes:
        lines.append((FAIL, f"taille différente : base {artifact.size_bytes} o, S3 {on_s3} o"))
    else:
        lines.append((OK, f"fichier sur le S3, taille identique à la base ({on_s3} o)"))
    return lines


def _check_webhook(
    db: Session,
    meeting: MeetingRow,
    artifact: ArtifactRow,
    events: list[WebhookDeliveryRow],
    expected_event: str,
) -> list[Line]:
    lines: list[Line] = []
    if len(events) != 1 or events[0].event_type != expected_event:
        found = ", ".join(e.event_type for e in events) or "aucun"
        return [(FAIL, f"webhooks recording.* : {found} (attendu : un seul {expected_event})")]
    event = events[0]
    lines.append((OK, f"un seul webhook recording.* : {event.event_type}"))
    if event.status is WebhookStatus.DELIVERED:
        lines.append(
            (OK, f"livré à Eduvia en {event.attempts} tentative(s) (2xx : signature acceptée)")
        )
    else:
        lines.append(
            (
                FAIL,
                f"non livré : statut {event.status.value}, {event.attempts} tentative(s), "
                f"dernière erreur : {event.last_error or 'aucune'}. "
                "Le récepteur tourne-t-il, sur la bonne URL (WEBHOOK_URL) ?",
            )
        )
    payload = event.payload
    data = payload.get("data", {})
    lines.append(
        (
            OK if data.get("recording_id") == artifact.id else FAIL,
            "recording_id du webhook = artefact en base",
        )
    )
    meta = payload.get("meeting", {}).get("metadata")
    lines.append(
        (OK if meta == meeting.metadata_ else FAIL, f"metadata de la réunion présentes : {meta}")
    )
    key = artifact.storage_key or ""
    leaked = bool(key) and key in json.dumps(payload)
    lines.append((FAIL if leaked else OK, "aucune clé S3 ni URL dans le webhook"))
    ended = db.scalar(
        select(WebhookDeliveryRow.created_at).where(
            WebhookDeliveryRow.meeting_id == meeting.id,
            WebhookDeliveryRow.event_type == webhooks.MEETING_ENDED,
        )
    )
    if ended is not None:
        lines.append(
            (OK if event.created_at >= ended else FAIL, "recording.* envoyé après meeting.ended")
        )
    if artifact.status is ArtifactStatus.READY:
        lines.append((INFO, f"durée annoncée : {data.get('duration_seconds')} s"))
    else:
        lines.append((INFO, f"motif annoncé : {data.get('reason')}"))
    return lines


# --- examen du fichier (option --probe) ------------------------------------------------------


def probe(storage: Storage, artifact: ArtifactRow, expected_duration: int | None) -> list[Line]:
    """Télécharge le MP4 puis, si `ffprobe` est installé, vérifie ses pistes et sa durée."""
    assert artifact.storage_key is not None
    folder = DESTINATION / artifact.meeting_id
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / Path(artifact.storage_key).name
    target.write_bytes(storage.get(artifact.storage_key))
    lines: list[Line] = [(INFO, f"fichier téléchargé : {target.resolve()}")]
    ffprobe = shutil.which("ffprobe")
    if ffprobe is None:
        lines.append(
            (INFO, "ffprobe absent : pas d'examen automatique (winget install Gyan.FFmpeg).")
        )
        return lines
    result = subprocess.run(  # noqa: S603 - ffprobe local, argument = fichier téléchargé
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "stream=codec_type,codec_name:format=duration",
            "-of",
            "json",
            str(target),
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    if result.returncode != 0:
        return [*lines, (FAIL, f"ffprobe ne lit pas le fichier : {result.stderr.strip()[:200]}")]
    info = json.loads(result.stdout)
    kinds = {s["codec_type"]: s["codec_name"] for s in info.get("streams", [])}
    lines.append(
        (OK if "video" in kinds else FAIL, f"piste vidéo : {kinds.get('video', 'ABSENTE')}")
    )
    lines.append(
        (OK if "audio" in kinds else FAIL, f"piste audio : {kinds.get('audio', 'ABSENTE')}")
    )
    duration = float(info.get("format", {}).get("duration", 0) or 0)
    lines.append((INFO, f"durée du fichier : {duration:.1f} s"))
    if expected_duration is not None:
        close = abs(duration - expected_duration) <= DURATION_TOLERANCE_SECONDS
        lines.append(
            (OK if close else FAIL, f"durée cohérente avec le webhook ({expected_duration} s)")
        )
    lines.append((INFO, "le partage d'écran visible se juge à l'œil : ouvre le fichier."))
    return lines


# --- ligne de commande ---------------------------------------------------------------------


def main(argv: list[str] | None = None, *, out: Callable[[str], None] = print) -> int:
    parser = argparse.ArgumentParser(prog="recording-check", description=__doc__.splitlines()[0])
    parser.add_argument("meeting_id", nargs="?", help="réunion à vérifier (défaut : la dernière)")
    parser.add_argument("--wait", type=int, default=0, help="secondes d'attente max de l'issue")
    parser.add_argument("--expect", choices=["ready", "failed"], default="ready")
    parser.add_argument("--probe", action="store_true", help="télécharge et examine le MP4")
    args = parser.parse_args(argv)
    try:
        settings = get_settings()
    except ConfigurationError as error:
        out(str(error))
        return 1
    storage = Storage.from_settings(settings)
    engine = create_db_engine(settings.database_url)
    try:
        with create_session_factory(engine)() as db:
            meeting_id = args.meeting_id or latest_meeting_id(db)
            if meeting_id is None:
                out("Aucune réunion en base.")
                return 1
            deadline = time.monotonic() + args.wait
            while True:
                report = evaluate(db, storage, meeting_id, expect=args.expect)
                if not report.pending or time.monotonic() >= deadline:
                    break
                time.sleep(3)
            lines = list(report.lines)
            if (
                args.probe
                and report.artifact is not None
                and report.artifact.status is ArtifactStatus.READY
            ):
                duration = next(
                    (
                        e.payload.get("data", {}).get("duration_seconds")
                        for e in _recording_events(db, meeting_id)
                    ),
                    None,
                )
                lines += probe(storage, report.artifact, duration)
    finally:
        engine.dispose()
    for level, text in lines:
        out(f"[{level:>5}] {text}")
    failed = report.failed or any(level == FAIL for level, _ in lines)
    out("\nRESULTAT : " + ("ECHEC" if failed else "TOUT EST OK"))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
