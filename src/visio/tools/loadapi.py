"""Charge de l'API de la plateforme (étape 20) : `uv run task load-api -- --api-key <clé>`.

Ce que `lk perf load-test` ne couvre pas : il se connecte directement à LiveKit. Ici on passe par
les vraies routes : création de M réunions, puis M × P personnes qui appellent `POST /v1/join`
en même temps (scénario 9 vu de l'API), `--rounds` fois (2e tour = reconnexions). Mesure : latence
(p50 / p95 / max), erreurs par code, personnes admises. Termine les réunions à la fin.

Critères (modifiables) : 0 erreur et p95 ≤ `--max-p95` (2 s par défaut : le test envoie
jusqu'à `--concurrency` = 100 requêtes à la fois, plus brutal que des arrivées étalées).
Code de sortie 1 sinon.
"""

from __future__ import annotations

import argparse
import asyncio
import math
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import httpx


@dataclass
class Stats:
    latencies: list[float] = field(default_factory=list)
    errors: dict[str, int] = field(default_factory=dict)
    admitted: int = 0

    def error(self, label: str) -> None:
        self.errors[label] = self.errors.get(label, 0) + 1


def percentile(values: list[float], q: float) -> float:
    """Percentile « rang le plus proche » ; 0 pour une liste vide."""
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(q / 100 * len(ordered)) - 1))
    return ordered[index]


def token_of(url: str) -> str:
    """Le jeton d'une URL `<domaine>/j/<jeton>`."""
    return url.rstrip("/").rsplit("/", 1)[-1]


async def create_meetings(client: httpx.AsyncClient, count: int) -> list[dict[str, str]]:
    now = datetime.now().astimezone()
    meetings: list[dict[str, str]] = []
    for number in range(1, count + 1):
        body = {
            "title": f"Charge API {number}",
            "starts_at": now.isoformat(timespec="seconds"),
            "ends_at": (now + timedelta(hours=2)).isoformat(timespec="seconds"),
            "waiting_room": False,
            "recording": False,
            "organizers": [{"display_name": "Charge", "external_id": f"load_{number}"}],
            "metadata": {"load_test": True},
        }
        response = await client.post("/v1/meetings", json=body)
        response.raise_for_status()
        data = response.json()
        meetings.append({"id": str(data["id"]), "token": token_of(data["participant_url"])})
    return meetings


async def _join(
    client: httpx.AsyncClient,
    gate: asyncio.Semaphore,
    stats: Stats,
    token: str,
    name: str,
    client_id: str,
) -> None:
    async with gate:
        started = time.perf_counter()
        try:
            response = await client.post(
                "/v1/join",
                json={"token": token, "display_name": name, "client_id": client_id},
            )
        except httpx.HTTPError as error:
            stats.error(type(error).__name__)
            return
        stats.latencies.append(time.perf_counter() - started)
    if response.status_code != 200:
        stats.error(f"HTTP {response.status_code}")
        return
    if response.json().get("status") == "admitted":
        stats.admitted += 1
    else:
        stats.error("non admis")


async def run_load(
    client: httpx.AsyncClient,
    *,
    meetings: int,
    participants: int,
    rounds: int,
    concurrency: int,
) -> tuple[Stats, list[dict[str, str]]]:
    created = await create_meetings(client, meetings)
    stats = Stats()
    gate = asyncio.Semaphore(concurrency)
    for _ in range(rounds):
        await asyncio.gather(
            *(
                _join(
                    client,
                    gate,
                    stats,
                    m["token"],
                    f"Eleve {i}-{p}",
                    f"load{i:03d}{p:03d}".ljust(16, "x"),
                )
                for i, m in enumerate(created, 1)
                for p in range(1, participants + 1)
            )
        )
    return stats, created


async def end_meetings(client: httpx.AsyncClient, created: list[dict[str, str]]) -> int:
    ended = 0
    for meeting in created:
        response = await client.post(f"/v1/meetings/{meeting['id']}/end")
        ended += 1 if response.status_code < 300 else 0
    return ended


def report(stats: Stats, expected: int, max_p95: float) -> tuple[list[str], bool]:
    p50, p95 = percentile(stats.latencies, 50), percentile(stats.latencies, 95)
    worst = max(stats.latencies, default=0.0)
    lines = [
        f"Appels POST /v1/join : {len(stats.latencies)} réponses sur {expected} attendues",
        f"Admis : {stats.admitted}",
        f"Latence : p50 {p50 * 1000:.0f} ms · p95 {p95 * 1000:.0f} ms · max {worst * 1000:.0f} ms",
    ]
    for label, count in sorted(stats.errors.items()):
        lines.append(f"ERREUR {label} : {count}")
    ok = not stats.errors and stats.admitted == expected and p95 <= max_p95
    lines.append(
        "RÉSULTAT : " + ("OK" if ok else f"ÉCHEC (0 erreur et p95 ≤ {max_p95:.1f} s exigés)")
    )
    return lines, ok


async def amain(args: argparse.Namespace) -> int:
    headers = {"Authorization": f"Bearer {args.api_key}"}
    async with httpx.AsyncClient(base_url=args.base, headers=headers, timeout=30.0) as client:
        try:
            stats, created = await run_load(
                client,
                meetings=args.meetings,
                participants=args.participants,
                rounds=args.rounds,
                concurrency=args.concurrency,
            )
        except httpx.HTTPError as error:
            print(f"Impossible de créer les réunions : {error}", file=sys.stderr)
            return 1
        health = await client.get("/health")
        ended = await end_meetings(client, created)
    expected = args.meetings * args.participants * args.rounds
    lines, ok = report(stats, expected, args.max_p95)
    print("\n".join(lines))
    print(f"/health après la charge : HTTP {health.status_code} · réunions terminées : {ended}")
    return 0 if ok and health.status_code == 200 else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Charge de l'API (étape 20).")
    parser.add_argument("--base", default="http://localhost:8000")
    parser.add_argument("--api-key", default=os.environ.get("VISIO_API_KEY", ""))
    parser.add_argument("--meetings", type=int, default=10)
    parser.add_argument("--participants", type=int, default=30)
    parser.add_argument("--rounds", type=int, default=2)
    parser.add_argument("--concurrency", type=int, default=100)
    parser.add_argument("--max-p95", type=float, default=2.0)
    args = parser.parse_args(argv)
    if not args.api_key:
        print("Clé d'API manquante : --api-key ou VISIO_API_KEY", file=sys.stderr)
        return 1
    return asyncio.run(amain(args))


if __name__ == "__main__":
    raise SystemExit(main())
