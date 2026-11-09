"""Rejoue des tâches, des webhooks ou des artefacts en échec (étape 17c).

    uv run task jobs-retry job_ab12...          # une tâche (ou plusieurs identifiants)
    uv run task jobs-retry evt_cd34...          # un webhook (même identifiant d'événement)
    uv run task jobs-retry art_ef56...          # un artefact : rejoue sa tâche
    uv run task jobs-retry --all                # tout ce qui est ABANDONNÉ
    uv run task jobs-retry --all mtg_777f...    # ... pour une seule réunion

Les tentatives repartent de zéro et l'heure de reprise passe à maintenant : l'API (ou le worker
`transcriber`) la prend au tour suivant. Rien n'est créé en double : rejouer deux fois la même
chose répond « déjà en attente », un webhook déjà livré est refusé. Voir `jobs-list`.
Code de retour 0 si tout est remis en file ou déjà en attente, 1 sinon.
"""

from __future__ import annotations

import sys
from collections.abc import Callable

from visio.config import ConfigurationError, get_settings
from visio.db import create_db_engine, create_session_factory
from visio.domain.clock import SystemClock
from visio.services.replay import Outcome, retry_all, retry_many

USAGE = "usage : jobs-retry <identifiant>... | jobs-retry --all [<réunion>]"


def parse(args: list[str]) -> tuple[bool, list[str]] | None:
    """`(tout, identifiants)` ou `None` si la ligne de commande est invalide."""
    everything = "--all" in args
    rest = [a for a in args if a != "--all"]
    if any(a.startswith("-") for a in rest):
        return None
    if everything and len(rest) > 1:
        return None
    if not everything and not rest:
        return None
    return everything, rest


def render(outcome: Outcome) -> str:
    detail = f" ({outcome.detail})" if outcome.detail else ""
    return f"  {outcome.identifier} : {outcome.result.value}{detail}"


def main(argv: list[str] | None = None, *, out: Callable[[str], None] = print) -> int:
    parsed = parse(argv if argv is not None else [])
    if parsed is None:
        out(USAGE)
        return 2
    everything, rest = parsed
    try:
        settings = get_settings()
    except ConfigurationError as error:
        out(str(error))
        return 1
    now = SystemClock().now()
    engine = create_db_engine(settings.database_url)
    try:
        with create_session_factory(engine)() as db:
            outcomes = (
                retry_all(db, now=now, meeting_id=rest[0] if rest else None)
                if everything
                else retry_many(db, rest, now=now)
            )
    finally:
        engine.dispose()
    if not outcomes:
        out("Rien d'abandonné à rejouer.")
        return 0
    for outcome in outcomes:
        out(render(outcome))
    return 0 if all(o.ok for o in outcomes) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
