"""Charge de l'API (étape 20) : statistiques et déroulé, avec un faux serveur HTTP."""

from __future__ import annotations

import asyncio
import json

import httpx

from visio.tools import loadapi


def test_percentile() -> None:
    assert loadapi.percentile([], 95) == 0.0
    assert loadapi.percentile([1.0], 95) == 1.0
    values = [float(i) for i in range(1, 101)]
    assert loadapi.percentile(values, 50) == 50.0
    assert loadapi.percentile(values, 95) == 95.0
    assert loadapi.percentile(values, 100) == 100.0


def test_token_of() -> None:
    assert loadapi.token_of("https://visio.example/j/abc.def.ghi") == "abc.def.ghi"
    assert loadapi.token_of("https://visio.example/j/abc/") == "abc"


def _server(*, deny_every: int = 0) -> httpx.MockTransport:
    state = {"meetings": 0, "joins": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/meetings" and request.method == "POST":
            state["meetings"] += 1
            n = state["meetings"]
            return httpx.Response(201, json={"id": n, "participant_url": f"https://x/j/tok{n}"})
        if request.url.path == "/v1/join":
            state["joins"] += 1
            body = json.loads(request.content)
            assert body["token"].startswith("tok")
            assert 16 <= len(body["client_id"]) <= 64
            if deny_every and state["joins"] % deny_every == 0:
                return httpx.Response(503, json={})
            return httpx.Response(200, json={"status": "admitted"})
        if request.url.path.endswith("/end"):
            return httpx.Response(200, json={})
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def test_run_load_counts_everything() -> None:
    async def scenario() -> tuple[loadapi.Stats, int]:
        async with httpx.AsyncClient(base_url="http://t", transport=_server()) as client:
            stats, created = await loadapi.run_load(
                client, meetings=3, participants=4, rounds=2, concurrency=5
            )
            return stats, await loadapi.end_meetings(client, created)

    stats, ended = asyncio.run(scenario())
    assert stats.admitted == 24
    assert stats.errors == {}
    assert ended == 3
    lines, ok = loadapi.report(stats, 24, 1.0)
    assert ok
    assert any("RÉSULTAT : OK" in line for line in lines)


def test_errors_make_the_result_fail() -> None:
    async def scenario() -> loadapi.Stats:
        async with httpx.AsyncClient(
            base_url="http://t", transport=_server(deny_every=4)
        ) as client:
            stats, _ = await loadapi.run_load(
                client, meetings=2, participants=4, rounds=1, concurrency=5
            )
            return stats

    stats = asyncio.run(scenario())
    assert stats.errors == {"HTTP 503": 2}
    _, ok = loadapi.report(stats, 8, 1.0)
    assert not ok


def test_missing_api_key(monkeypatch: object) -> None:
    import os

    os.environ.pop("VISIO_API_KEY", None)
    assert loadapi.main([]) == 1
