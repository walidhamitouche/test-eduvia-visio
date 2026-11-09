"""Liste les enregistrements présents sur le S3 que lit l'API (étape 14a, vérification manuelle).

    uv run task recordings-list

Affiche chaque objet `meetings/<id>/recordings/<uuid>.mp4` avec sa taille et, pour les réunions
connues de la base, l'état de l'artefact. Lecture seule : ne modifie ni le S3 ni la base.
"""

from __future__ import annotations

import sys
from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from visio.config import ConfigurationError, get_settings
from visio.db import create_db_engine, create_session_factory
from visio.db.models import ArtifactKind, ArtifactRow, JobRow
from visio.services.storage import Storage, StorageError

PREFIX = "meetings/"


def format_size(size: int) -> str:
    value = float(size)
    for unit in ("o", "Ko", "Mo", "Go"):
        if value < 1024 or unit == "Go":
            return f"{value:.0f} {unit}" if unit == "o" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} o"  # inatteignable, pour mypy


def render(objects: list[tuple[str, int]], states: dict[str, tuple[str, str | None]]) -> list[str]:
    """Lignes à afficher. `states` : clé d'objet -> (statut de l'artefact, egress_id)."""
    lines: list[str] = []
    for key, size in sorted(objects):
        if "/recordings/" not in key:
            continue
        status, egress_id = states.get(key, ("absent de la base", None))
        suffix = f" · {egress_id}" if egress_id else ""
        lines.append(f"  {format_size(size):>9}  {key}  [{status}{suffix}]")
    return lines


def _detail(db: Session, artifact: ArtifactRow) -> str:
    """Pourquoi un artefact n'a pas (encore) de fichier : Egress a-t-il démarré ? la tâche ?"""
    parts = [f"egress_id : {artifact.egress_id or 'aucun (Egress pas encore démarré)'}"]
    job = db.scalar(
        select(JobRow).where(
            JobRow.kind == "start_recording",
            JobRow.payload["artifact_id"].as_string() == artifact.id,
        )
    )
    if job is not None:
        parts.append(f"tâche de démarrage : {job.status.value}, {job.attempts} essai(s)")
        if job.last_error:
            parts.append(f"dernière erreur : {job.last_error}")
    if artifact.error:
        parts.append(f"erreur : {artifact.error}")
    return " · ".join(parts)


def main(argv: list[str] | None = None, *, out: Callable[[str], None] = print) -> int:
    del argv
    try:
        settings = get_settings()
    except ConfigurationError as error:
        out(str(error))
        return 1
    storage = Storage.from_settings(settings)
    try:
        objects = storage.list_objects(PREFIX)
    except StorageError as error:
        out(f"S3 : {error}")
        return 1
    engine = create_db_engine(settings.database_url)
    try:
        with create_session_factory(engine)() as db:
            rows = db.scalars(
                select(ArtifactRow).where(ArtifactRow.kind == ArtifactKind.RECORDING)
            ).all()
            states = {r.storage_key: (r.status.value, r.egress_id) for r in rows if r.storage_key}
            details = {r.storage_key: _detail(db, r) for r in rows if r.storage_key}
    finally:
        engine.dispose()
    lines = render(objects, states)
    out(f"{len(lines)} enregistrement(s) sur le S3 (bucket {storage.bucket}) :")
    for line in lines:
        out(line)
    waiting = [k for k, (st, _) in states.items() if k not in {o[0] for o in objects}]
    if waiting:
        out(f"{len(waiting)} artefact(s) en base sans fichier (en cours, ou en échec) :")
        for key in sorted(waiting):
            out(f"  {key}  [{states[key][0]}]")
            out(f"      {details[key]}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
