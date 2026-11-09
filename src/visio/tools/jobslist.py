"""Liste ce qui est en échec et ce qu'on peut rejouer (étape 17c).

    uv run task jobs-list                    # tout
    uv run task jobs-list mtg_777f5e44...    # une réunion

Quatre familles : tâches (`failed` / `abandoned`), webhooks abandonnés ou en relance, artefacts
(enregistrements, transcriptions) et pistes audio en échec. Chaque ligne donne l'identifiant à
passer à `uv run task jobs-retry`. Lecture seule : ne modifie ni la base ni le S3. Code de retour
0 s'il n'y a rien en échec, 1 sinon (utilisable dans un script de supervision).
"""

from __future__ import annotations

import sys
from collections.abc import Callable

from visio.config import ConfigurationError, get_settings
from visio.db import create_db_engine, create_session_factory
from visio.domain.slot import to_paris
from visio.services.replay import Failure, list_failures


def render(failure: Failure) -> list[str]:
    where = f"  [{failure.meeting_id}]" if failure.meeting_id else ""
    attempts = "" if failure.attempts is None else f", {failure.attempts} essai(s)"
    lines = [f"  {failure.identifier}  {failure.label} : {failure.status}{attempts}{where}"]
    if failure.next_attempt_at is not None:
        lines.append(
            f"      prochain essai : {to_paris(failure.next_attempt_at):%Y-%m-%d %H:%M:%S}"
        )
    if failure.error:
        lines.append(f"      erreur : {failure.error}")
    if failure.replay:
        lines.append(f"      rejouer : uv run task jobs-retry {failure.replay}")
    elif failure.note:
        lines.append(f"      {failure.note}")
    return lines


def render_all(failures: list[Failure]) -> list[str]:
    if not failures:
        return ["Rien en échec."]
    lines: list[str] = []
    for category in ("tâche", "webhook", "artefact", "piste audio"):
        group = [f for f in failures if f.category == category]
        if not group:
            continue
        lines.append(f"{category}s en échec : {len(group)}")
        for failure in group:
            lines.extend(render(failure))
        lines.append("")
    return lines[:-1]


def main(argv: list[str] | None = None, *, out: Callable[[str], None] = print) -> int:
    args = argv if argv is not None else []
    meeting_id = args[0] if args else None
    try:
        settings = get_settings()
    except ConfigurationError as error:
        out(str(error))
        return 1
    engine = create_db_engine(settings.database_url)
    try:
        with create_session_factory(engine)() as db:
            failures = list_failures(db, meeting_id=meeting_id)
    finally:
        engine.dispose()
    for line in render_all(failures):
        out(line)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
