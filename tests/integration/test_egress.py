"""Étape 13c : passerelle Egress et outil `egresscheck`, sans Docker.

Contre un faux Egress (transport HTTP simulé) et un S3 simulé (moto).

Le vrai Egress se vérifie à la main : `docker-dev.ps1 up`, puis `uv run task egress-check`.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable, Iterator
from typing import Any

import httpx
import jwt
import pytest
from moto.server import ThreadedMotoServer

from visio.domain.errors import MediaServerError
from visio.services.egress import EgressGateway, EgressUnavailableError
from visio.services.storage import Storage
from visio.tools.egresscheck import run_check

PORT = 5598
KEY = "devkey-" + "k" * 20
SECRET = "egress-test-secret-" + "x" * 40
MP4 = b"\x00\x00\x00\x20ftypisom" + b"\x00" * 2000


@pytest.fixture(scope="module")
def s3_endpoint() -> Iterator[str]:
    server = ThreadedMotoServer(ip_address="127.0.0.1", port=PORT, verbose=False)
    server.start()
    try:
        yield f"http://127.0.0.1:{PORT}"
    finally:
        server.stop()


@pytest.fixture
def storage(s3_endpoint: str) -> Storage:
    store = Storage(
        endpoint=s3_endpoint,
        bucket=f"visio-{uuid.uuid4().hex[:12]}",
        access_key="s3-access",
        secret_key="test-s3-secret-" + "e" * 40,
        region="eu-west-3",
    )
    store.ensure_bucket()
    return store


class FakeEgress:
    """Simule le service Egress derrière l'API Twirp de LiveKit, et dépose le fichier sur le S3."""

    def __init__(
        self,
        storage: Storage,
        *,
        unavailable_calls: int = 0,
        outcome: str = "complete",  # complete | failed | no_file | not_mp4 | tiny
        numeric_status: bool = False,
    ) -> None:
        self.storage = storage
        self.unavailable_calls = unavailable_calls
        self.outcome = outcome
        self.numeric_status = numeric_status
        self.requests: list[tuple[str, dict[str, Any], str]] = []
        self.key: str | None = None
        self.state = "none"  # none | starting | active | complete | failed
        self.polls = 0

    def _status(self, name: str) -> Any:
        if self.numeric_status:
            return {"starting": 0, "active": 1, "complete": 3, "failed": 4}[name]
        return "EGRESS_" + name.upper()

    def _info(self) -> dict[str, Any]:
        info: dict[str, Any] = {"egressId": "EG_test", "status": self._status(self.state)}
        if self.state == "failed":
            info["error"] = "chrome a planté"
        if self.state == "complete" and self.key:
            info["fileResults"] = [{"filename": self.key}]
        return info

    def handler(self, request: httpx.Request) -> httpx.Response:
        method = request.url.path.rsplit("/", 1)[-1]
        body = json.loads(request.content or b"{}")
        self.requests.append((method, body, request.headers["authorization"]))
        if method == "StartWebEgress":
            if self.unavailable_calls > 0:
                self.unavailable_calls -= 1
                return httpx.Response(
                    503, json={"code": "unavailable", "msg": "no response from egress service"}
                )
            self.key = body["file_outputs"][0]["filepath"]
            self.state = "starting"
            return httpx.Response(200, json=self._info())
        if method == "ListEgress":
            self.polls += 1
            if self.state == "starting":
                self.state = "active"
            elif self.state == "ending":
                self._finish()
            items = [self._info()] if self.state != "none" else []
            return httpx.Response(200, json={"items": items})
        if method == "StopEgress":
            self._finish()
            return httpx.Response(200, json=self._info())
        return httpx.Response(404, json={"code": "bad_route"})

    def _finish(self) -> None:
        if self.outcome == "failed":
            self.state = "failed"
            return
        self.state = "complete"
        assert self.key is not None
        if self.outcome == "complete":
            self.storage.put(self.key, MP4, content_type="video/mp4")
        elif self.outcome == "not_mp4":
            self.storage.put(self.key, b"x" * 4000)
        elif self.outcome == "tiny":
            self.storage.put(self.key, b"\x00\x00\x00\x20ftyp")

    def gateway(self) -> EgressGateway:
        client = httpx.Client(transport=httpx.MockTransport(self.handler))
        return EgressGateway("ws://localhost:7880", KEY, SECRET, client=client)


class Clock:
    """Horloge simulée : `sleep` fait avancer le temps, sans attendre pour de vrai."""

    def __init__(self) -> None:
        self.t = 0.0
        self.sleeps: list[float] = []

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.t += seconds

    def now(self) -> float:
        return self.t


def check(fake: FakeEgress, storage: Storage, **options: Any) -> tuple[list[str], list[str], Clock]:
    clock = Clock()
    logs: list[str] = []
    problems = run_check(
        fake.gateway(),
        storage,
        sleep=clock.sleep,
        now=clock.now,
        log=logs.append,
        **options,
    )
    return problems, logs, clock


# --- passerelle -------------------------------------------------------------------------------


def test_requests_are_authenticated_with_a_record_grant(storage: Storage) -> None:
    fake = FakeEgress(storage)
    fake.gateway().start_web_to_file("http://127.0.0.1:7880", "healthcheck/a.mp4")
    method, body, authorization = fake.requests[0]
    assert method == "StartWebEgress"
    assert body == {
        "url": "http://127.0.0.1:7880",
        "file_outputs": [{"filepath": "healthcheck/a.mp4"}],
    }
    claims = jwt.decode(
        authorization.removeprefix("Bearer "), SECRET, algorithms=["HS256"], options={}
    )
    assert claims["iss"] == KEY
    assert claims["video"] == {"roomRecord": True}
    assert claims["exp"] - claims["nbf"] <= 120  # jeton de courte durée


def test_no_s3_credentials_in_orders(storage: Storage) -> None:
    """Les clés S3 restent dans la configuration d'Egress : elles ne transitent jamais par l'API."""
    fake = FakeEgress(storage)
    fake.gateway().start_web_to_file("http://127.0.0.1:7880", "healthcheck/a.mp4")
    dumped = json.dumps(fake.requests)
    assert "s3" not in dumped.lower()
    assert "secret" not in dumped.lower()


@pytest.mark.parametrize("numeric", [False, True])
def test_status_is_read_from_names_or_numbers(storage: Storage, numeric: bool) -> None:
    fake = FakeEgress(storage, numeric_status=numeric)
    gateway = fake.gateway()
    assert gateway.start_web_to_file("http://x", "a.mp4").status == "starting"
    assert gateway.list("EG_test")[0].status == "active"
    assert gateway.stop("EG_test").status == "complete"


def test_snake_case_responses_are_understood() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "egress_id": "EG_9",
                "status": "EGRESS_COMPLETE",
                "file_results": [{"filename": "meetings/m/a.mp4"}],
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    info = EgressGateway("http://l", KEY, SECRET, client=client).stop("EG_9")
    assert (info.egress_id, info.status, info.files) == ("EG_9", "complete", ("meetings/m/a.mp4",))


def test_no_egress_worker_is_reported_as_unavailable(storage: Storage) -> None:
    fake = FakeEgress(storage, unavailable_calls=1)
    with pytest.raises(EgressUnavailableError, match="Egress"):
        fake.gateway().start_web_to_file("http://x", "a.mp4")


def test_livekit_down_is_a_media_server_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refusé")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(MediaServerError, match="ne répond pas"):
        EgressGateway("http://l", KEY, SECRET, client=client).list()


def test_refusal_message_never_contains_the_secret() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"code": "unauthenticated", "msg": "invalid token"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(MediaServerError) as caught:
        EgressGateway("http://l", KEY, SECRET, client=client).list()
    assert SECRET not in str(caught.value)
    assert "401" in str(caught.value)


# --- outil egress-check -----------------------------------------------------------------------


def test_check_passes_and_cleans_up(storage: Storage) -> None:
    fake = FakeEgress(storage)
    problems, logs, clock = check(fake, storage)
    assert problems == []
    assert any("MP4 présent" in line for line in logs)
    assert fake.key is not None
    assert not storage.exists(fake.key)  # l'objet de test est supprimé
    assert 5.0 in clock.sleeps  # l'enregistrement a bien tourné quelques secondes


def test_check_waits_for_egress_to_register(storage: Storage) -> None:
    fake = FakeEgress(storage, unavailable_calls=3)
    problems, _, clock = check(fake, storage)
    assert problems == []
    assert clock.t >= 3  # trois relances avant que le service réponde


def test_check_gives_up_when_egress_never_answers(storage: Storage) -> None:
    fake = FakeEgress(storage, unavailable_calls=10_000)
    problems, _, clock = check(fake, storage, start_timeout=10)
    assert len(problems) == 1
    assert "Egress ne répond pas après 10 s" in problems[0]
    assert clock.t <= 12  # n'attend pas indéfiniment


def test_check_reports_a_failed_recording_with_its_reason(storage: Storage) -> None:
    problems, _, _ = check(FakeEgress(storage, outcome="failed"), storage)
    assert problems == ["l'enregistrement s'est terminé en « failed » : chrome a planté"]


def test_check_detects_a_missing_file(storage: Storage) -> None:
    problems, _, _ = check(FakeEgress(storage, outcome="no_file"), storage)
    assert len(problems) == 1
    assert "n'est pas arrivé sur le S3" in problems[0]


def test_check_detects_a_file_that_is_not_an_mp4(storage: Storage) -> None:
    fake = FakeEgress(storage, outcome="not_mp4")
    problems, _, _ = check(fake, storage)
    assert any("pas un MP4" in problem for problem in problems)
    assert fake.key is not None
    assert not storage.exists(fake.key)  # nettoyé même en cas d'échec


def test_check_detects_a_suspiciously_small_file(storage: Storage) -> None:
    problems, _, _ = check(FakeEgress(storage, outcome="tiny"), storage)
    assert any("octets seulement" in problem for problem in problems)


def test_check_stops_a_recording_left_running(storage: Storage) -> None:
    """Si l'outil s'interrompt (délai dépassé), il ne laisse pas d'enregistrement en cours."""
    fake = FakeEgress(storage)
    original: Callable[[httpx.Request], httpx.Response] = fake.handler

    def stuck(request: httpx.Request) -> httpx.Response:
        # ListEgress ne dit jamais « active » : l'outil abandonne, puis doit arrêter l'egress.
        if request.url.path.endswith("ListEgress") and fake.state in ("starting", "active"):
            fake.requests.append(("ListEgress", {}, request.headers["authorization"]))
            return httpx.Response(
                200, json={"items": [fake._info() | {"status": "EGRESS_STARTING"}]}
            )
        return original(request)

    gateway = EgressGateway(
        "ws://localhost:7880",
        KEY,
        SECRET,
        client=httpx.Client(transport=httpx.MockTransport(stuck)),
    )
    clock = Clock()
    problems = run_check(
        gateway, storage, sleep=clock.sleep, now=clock.now, log=lambda _: None, finish_timeout=5
    )
    assert problems  # l'état final n'est jamais atteint
    assert "StopEgress" in [method for method, _, _ in fake.requests]
