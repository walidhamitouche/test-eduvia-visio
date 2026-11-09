"""Enregistre les N premières salles d'un test de charge (étape 20b), puis les arrête.

    uv run task load-record -- --rooms 2 --seconds 1800 [--api-key K --api-secret S]

Les salles `charge-1`… `charge-N` sont celles de `deploy/dev/test-charge.ps1` : même mise en page
qu'en production (`start_room_composite`), même S3. Les clés LiveKit viennent de la configuration,
sauf `--api-key` / `--api-secret` (le script de charge passe celles du serveur testé). Sortie : le
statut final de chaque enregistrement, pour compter les échecs.
"""

from __future__ import annotations

import argparse
import time
import uuid
from collections.abc import Callable

from visio.config import ConfigurationError, get_settings
from visio.services.egress import EgressGateway, EgressInfo, RecorderGateway
from visio.services.storage import Storage

FINISH_TIMEOUT = 120.0


def record_rooms(
    egress: RecorderGateway,
    rooms: int,
    seconds: float,
    *,
    prefix: str | None = None,
    sleep: Callable[[float], None] = time.sleep,
    log: Callable[[str], None] = print,
) -> list[EgressInfo]:
    """Démarre `rooms` enregistrements, attend `seconds`, les arrête et rend leur état final."""
    run = prefix or f"charge/{uuid.uuid4().hex[:8]}"
    started: dict[str, str] = {}
    failures: list[EgressInfo] = []
    for number in range(1, rooms + 1):
        room = f"charge-{number}"
        try:
            info = egress.start_room_composite(room, f"{run}/{room}.mp4")
        except Exception as error:  # noqa: BLE001 : on veut tout compter
            log(f"  ECHEC au démarrage de {room} : {error}")
            failures.append(EgressInfo("", "failed", str(error), ()))
            continue
        started[room] = info.egress_id
        log(f"  {room} : enregistrement {info.egress_id} ({info.status})")
    sleep(seconds)
    finals: list[EgressInfo] = list(failures)
    for room, egress_id in started.items():
        try:
            # D'abord l'état réel : un enregistrement déjà tombé en échec ne s'arrête plus (412) et
            # on perdrait la cause. On la garde et on la montre.
            current = [i for i in egress.list(egress_id=egress_id) if i.egress_id == egress_id]
            info = current[0] if current else EgressInfo(egress_id, "failed", "introuvable", ())
            if not info.is_final:
                info = egress.stop(egress_id)
            waited = 0.0
            while not info.is_final and waited < FINISH_TIMEOUT:
                sleep(2)
                waited += 2
                matching = [i for i in egress.list(egress_id=egress_id) if i.egress_id == egress_id]
                if matching:
                    info = matching[0]
        except Exception as error:  # noqa: BLE001
            log(f"  ECHEC à l'arrêt de {room} : {error}")
            finals.append(EgressInfo(egress_id, "failed", str(error), ()))
            continue
        log(f"  {room} : {info.status} {info.error} {', '.join(info.files)}")
        finals.append(info)
    return finals


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Enregistre des salles de charge (étape 20b).")
    parser.add_argument("--rooms", type=int, default=2)
    parser.add_argument("--seconds", type=float, default=60.0)
    parser.add_argument("--api-key", default="")
    parser.add_argument("--api-secret", default="")
    args = parser.parse_args(argv)
    try:
        settings = get_settings()
    except ConfigurationError as error:
        print(f"Configuration invalide : {error}")
        return 1
    egress = EgressGateway(
        settings.livekit_url,
        args.api_key or settings.livekit_api_key,
        args.api_secret or settings.livekit_api_secret.get_secret_value(),
        template_url=settings.recording_template_url,
        segment_seconds=settings.recording_segment_seconds,
    )
    results = record_rooms(egress, args.rooms, args.seconds)
    storage = Storage.from_settings(settings)
    for info in results:
        for key in info.files:
            size = storage.size(key)
            if size and info.duration_seconds:
                per_hour = size / info.duration_seconds * 3600 / 1e9
                mb = size / 1e6
                print(f"  {key} : {mb:.0f} Mo en {info.duration_seconds} s = {per_hour:.2f} Go/h")
    bad = [r for r in results if r.status != "complete"]
    print(f"Enregistrements : {len(results) - len(bad)} complet(s), {len(bad)} en échec")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
