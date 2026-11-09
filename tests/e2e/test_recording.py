"""Étape 14c : l'indicateur d'enregistrement et la recette navigateur de l'enregistrement.

Deux familles de tests :

- **Sans Egress** (`uv run task e2e`, LiveKit natif) : l'indicateur « Enregistrement en cours » ne
  dépend jamais de l'option `recording` seule. Il vient du serveur de médias (`room.isRecording`),
  donc il n'apparaît que si un enregistrement tourne réellement (DECISIONS.md, point 87). Sans
  `recording`, il n'y a ni avis préalable, ni indicateur, ni artefact, ni événement `recording.*`.
  Avec `recording` mais sans Egress, l'avis préalable est affiché, l'indicateur ne l'est pas.
- **Avec Egress** (`uv run task e2e-docker`, après `docker-dev.ps1 up`) : le **scénario 6, partie
  enregistrement**. Deux navigateurs, un partage d'écran, l'indicateur visible par tous, puis le MP4
  sur le S3 et `recording.ready` signé. Le fichier est laissé dans `data/recordings/<réunion>/`
  pour l'écouter et le regarder (partage d'écran visible : jugement à l'œil, voir la checklist).

Durée de l'essai réel : 120 s de réunion par défaut ; `E2E_RECORDING_SECONDS=30` pour l'abréger.
"""

from __future__ import annotations

import os
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select

from tests.e2e.conftest import Backend, NewPage
from tests.e2e.test_interface import enter_room, open_link
from tests.e2e.test_room_management import tiles, wait_until
from visio.db import create_db_engine, create_session_factory
from visio.db.models import ArtifactKind, ArtifactRow, ArtifactStatus
from visio.services.storage import Storage
from visio.tools import recordingcheck

NOTICE = "Cette réunion sera enregistrée"
INDICATOR = "recording-indicator"
OBSERVE_SECONDS = 4  # « jamais » se prouve en regardant un peu : plus long que l'envoi d'un signal
RECORDING_SECONDS = int(os.environ.get("E2E_RECORDING_SECONDS", "120"))


def artifact_count(backend: Backend, meeting_id: str) -> int:
    path = backend.db_url.removeprefix("sqlite:///")
    # `closing` : le `with` de sqlite3 valide la transaction mais ne ferme pas la connexion.
    with closing(sqlite3.connect(path, timeout=10)) as connection:
        row = connection.execute(
            "SELECT COUNT(*) FROM artifacts WHERE meeting_id = ?", (meeting_id,)
        ).fetchone()
    return int(row[0])


def events_of(backend: Backend, meeting_id: str) -> list[dict[str, Any]]:
    return [e for e in backend.receiver.events() if e["meeting"]["id"] == meeting_id]


def end_from_the_ui(page: Any) -> None:
    page.get_by_test_id("end-meeting").click()
    page.get_by_test_id("end-confirm").click()
    page.get_by_role("heading", name="Réunion terminée").wait_for(timeout=20_000)


# --- sans enregistrement : ni avis, ni indicateur, ni artefact, ni événement --------------------


def test_without_recording_there_is_no_notice_no_indicator_no_artifact_no_event(
    backend: Backend, needs_livekit: None, new_page: NewPage
) -> None:
    meeting = backend.create_meeting(recording=False, metadata={"eduvia_session_id": 1})
    marie, lina = new_page(), new_page()

    open_link(marie, backend, meeting["organizer_urls"][0])
    marie.get_by_role("button", name="Rejoindre la réunion").wait_for()
    assert marie.get_by_text(NOTICE).count() == 0  # pas d'avis préalable
    enter_room(marie)
    open_link(lina, backend, meeting["participant_url"])
    enter_room(lina, name="Lina Benali")
    tiles(marie, 2)

    time.sleep(OBSERVE_SECONDS)
    for page in (marie, lina):
        assert page.get_by_test_id(INDICATOR).count() == 0

    end_from_the_ui(marie)
    wait_until(
        lambda: any(e["type"] == "meeting.ended" for e in events_of(backend, meeting["id"])),
        what="meeting.ended",
    )
    time.sleep(2)  # laisse au planificateur le temps d'émettre ce qu'il aurait à émettre
    assert artifact_count(backend, meeting["id"]) == 0
    assert not [e for e in events_of(backend, meeting["id"]) if e["type"].startswith("recording.")]


# --- avec l'option mais sans enregistrement réel : l'indicateur ne ment pas ---------------------


def test_the_indicator_follows_the_real_recording_not_the_option(
    backend: Backend, needs_livekit: None, new_page: NewPage
) -> None:
    if backend.docker:
        pytest.skip("avec Egress l'enregistrement est réel : voir le test du scénario 6")
    meeting = backend.create_meeting(recording=True)
    marie, lina = new_page(), new_page()

    open_link(lina, backend, meeting["participant_url"])
    lina.get_by_role("button", name="Rejoindre la réunion").wait_for()
    assert lina.get_by_text(NOTICE).count() == 1  # l'avis préalable est bien là
    open_link(marie, backend, meeting["organizer_urls"][0])
    enter_room(marie)
    enter_room(lina, name="Lina Benali")
    tiles(marie, 2)

    # Ici aucun Egress ne tourne : la réunion « doit » être enregistrée, elle ne l'est pas.
    time.sleep(OBSERVE_SECONDS)
    for page in (marie, lina):
        assert page.get_by_test_id(INDICATOR).count() == 0


# --- le partage d'écran circule (nécessaire à la recette réelle) --------------------------------


def test_screen_share_reaches_the_other_participant_and_stops(
    backend: Backend, needs_livekit: None, new_page: NewPage
) -> None:
    meeting = backend.create_meeting(recording=False)
    marie, lina = new_page(), new_page()
    open_link(marie, backend, meeting["organizer_urls"][0])
    enter_room(marie)
    open_link(lina, backend, meeting["participant_url"])
    enter_room(lina, name="Lina Benali")
    tiles(marie, 2)

    marie.get_by_test_id("toggle-share").click()
    lina.get_by_test_id("screen-share").wait_for(timeout=20_000)
    marie.get_by_test_id("toggle-share").click()
    lina.get_by_test_id("screen-share").wait_for(state="detached", timeout=20_000)


# --- scénario 6 (partie enregistrement), pour de vrai : Egress + S3 de dev ----------------------


def test_scenario_6_recording_with_screen_share_ends_up_on_the_s3(
    backend: Backend, needs_livekit: None, needs_egress: None, new_page: NewPage
) -> None:
    assert backend.s3 is not None
    metadata = {"eduvia_session_id": 6060}
    meeting = backend.create_meeting(recording=True, metadata=metadata)
    meeting_id = meeting["id"]
    lina_url = backend.create_link(
        meeting_id, role="participant", display_name="Lina Benali", external_id="stu_981"
    )
    marie, lina = new_page(), new_page()
    open_link(marie, backend, meeting["organizer_urls"][0])
    marie.get_by_role("button", name="Rejoindre la réunion").wait_for()
    assert marie.get_by_text(NOTICE).count() == 1  # avis préalable
    enter_room(marie)
    open_link(lina, backend, lina_url)
    enter_room(lina)
    tiles(marie, 2)

    # L'indicateur s'allume chez tout le monde, une fois l'enregistrement réellement démarré.
    marie.get_by_test_id(INDICATOR).wait_for(timeout=90_000)
    lina.get_by_test_id(INDICATOR).wait_for(timeout=30_000)
    recording_since = time.monotonic()

    # Partage d'écran : visible chez Lina ; le micro et la caméra simulés des deux continuent.
    marie.get_by_test_id("toggle-share").click()
    lina.get_by_test_id("screen-share").wait_for(timeout=20_000)

    time.sleep(RECORDING_SECONDS)

    # Egress rejoint la salle mais reste invisible : ni vignette, ni ligne de présence.
    tiles(marie, 2)
    report = backend.get(f"/v1/meetings/{meeting_id}/attendance")
    assert {p["display_name"] for p in report["participants"]} == {"Marie Durand", "Lina Benali"}

    recorded_for = time.monotonic() - recording_since
    end_from_the_ui(marie)

    def ready() -> dict[str, Any] | None:
        return next(
            (e for e in events_of(backend, meeting_id) if e["type"] == "recording.ready"), None
        )

    wait_until(lambda: ready() is not None, seconds=150, what="recording.ready")
    event = ready()
    assert event is not None
    types = [e["type"] for e in events_of(backend, meeting_id)]
    assert types.index("recording.ready") > types.index("meeting.ended")
    assert "recording.failed" not in types
    assert event["meeting"]["metadata"] == metadata
    assert backend.receiver.valid_signatures()  # webhooks signés (scénario 6)
    assert event["data"]["size_bytes"] > 0
    duration = event["data"]["duration_seconds"]
    # Egress commence un peu après l'indicateur (signal de départ) : tolérance de 20 s en moins.
    assert duration is None or recorded_for - 20 <= duration <= recorded_for + 60

    # Le fichier est sur le S3 : mêmes contrôles que `uv run task recording-check --probe`.
    storage = Storage(
        endpoint=backend.s3["S3_ENDPOINT"],
        bucket=backend.s3["S3_BUCKET"],
        access_key=backend.s3["S3_ACCESS_KEY"],
        secret_key=backend.s3["S3_SECRET_KEY"],
        region=backend.s3.get("S3_REGION", "eu-west-3"),
    )
    engine = create_db_engine(backend.db_url)
    try:
        with create_session_factory(engine)() as db:
            artifact = db.scalars(
                select(ArtifactRow).where(
                    ArtifactRow.meeting_id == meeting_id,
                    ArtifactRow.kind == ArtifactKind.RECORDING,
                )
            ).one()
            assert artifact.status is ArtifactStatus.READY
            key = str(artifact.storage_key)
            assert key.startswith(f"meetings/{meeting_id}/recordings/")
            assert key.endswith(".mp4")

            # Le webhook vient d'arriver chez le récepteur : sa livraison est marquée en base
            # quelques millisecondes plus tard, on laisse ce temps au contrôle.
            deadline = time.monotonic() + 15
            while True:
                report = recordingcheck.evaluate(db, storage, meeting_id)
                if not report.pending or time.monotonic() > deadline:
                    break
                time.sleep(0.5)
            checks = [*report.lines, *recordingcheck.probe(storage, artifact, duration)]
    finally:
        engine.dispose()
    failures = [text for level, text in checks if level == recordingcheck.FAIL]
    assert not failures, failures
    assert storage.get(key)[4:8] == b"ftyp"  # c'est bien un MP4
    saved = Path(recordingcheck.DESTINATION) / meeting_id
    print(f"\nMP4 à regarder (partage d'écran visible ?) : {saved.resolve()}")
