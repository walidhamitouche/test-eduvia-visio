"""Tests de bout en bout (E2E) : vrai navigateur (Chromium, micro et caméra simulés), vrai backend,
vrai LiveKit. Lancés à part : `uv run task e2e` (marqueur `e2e`, exclus de `uv run task check`).

Prérequis (une fois) :
  1. `npm --prefix web ci` puis `npm --prefix web run build` (interface construite) ;
  2. un navigateur : Microsoft Edge (déjà présent sous Windows) ou Google Chrome sont utilisés
     tels quels, **sans rien télécharger**. Seulement s'il n'y en a aucun :
     `uv run playwright install chromium`. Variable `E2E_BROWSER` (`msedge`, `chrome` ou
     `chromium`) pour imposer un choix ;
  3. le binaire LiveKit : dans le PATH, dans `deploy/dev/` (déjà là pour le développement), ou
     indiqué par la variable `LIVEKIT_SERVER_BIN`. Sans lui, les tests qui entrent en salle sont
     ignorés (les autres tournent).
Le backend et LiveKit sont lancés par les fixtures sur des ports libres, avec une base temporaire :
rien de ce qui tourne déjà n'est touché.

Mode Docker (étape 13, non-régression) : `uv run task e2e-docker` (option `--livekit-docker`)
vise le LiveKit du Docker de dev (`docker-dev.ps1 up`, port 7880) au lieu d'en lancer un. Le
backend de test écoute alors sur le port 8000, vers lequel ce LiveKit envoie ses webhooks
(`host.docker.internal:8000`) : arrêter avant tout `uv run task dev-lan`.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest

from tests.conftest import SECRET_API_KEY, valid_env
from visio.db.migrate import upgrade

ROOT = Path(__file__).resolve().parents[2]
DIST = ROOT / "web" / "dist"
PARIS = ZoneInfo("Europe/Paris")
LIVEKIT_KEY = "lk-key"
LIVEKIT_SECRET = valid_env()["LIVEKIT_API_SECRET"]


DOCKER_LIVEKIT_URL = "ws://127.0.0.1:7880"
DOCKER_BACKEND_PORT = 8000  # port du webhook configuré dans deploy/dev/docker-dev.ps1
# Le S3 de dev (SeaweedFS) est celui où Egress écrit : en mode Docker, le backend de test doit
# donc lire le même bucket que `uv run task dev` (valeurs de .env.development, via dotenvx).
S3_VARIABLES = ("S3_ENDPOINT", "S3_BUCKET", "S3_ACCESS_KEY", "S3_SECRET_KEY")


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--livekit-docker",
        action="store_true",
        default=False,
        help="utilise le LiveKit du Docker de dev (docker-dev.ps1 up) au lieu d'en lancer un",
    )


def use_docker(config: pytest.Config) -> bool:
    return bool(config.getoption("--livekit-docker"))


def livekit_credentials(config: pytest.Config) -> tuple[str, str]:
    """Clé et secret LiveKit : ceux de .env.development en mode Docker (injectés par dotenvx)."""
    if not use_docker(config):
        return LIVEKIT_KEY, LIVEKIT_SECRET
    key, secret = os.environ.get("LIVEKIT_API_KEY"), os.environ.get("LIVEKIT_API_SECRET")
    if not key or not secret:
        msg = "clés LiveKit absentes : lance  uv run task e2e-docker  (dotenvx les injecte)"
        raise RuntimeError(msg)
    return key, secret


def docker_s3_env() -> dict[str, str] | None:
    """Les réglages S3 réels (dev), ou `None` s'ils ne sont pas dans l'environnement."""
    values = {name: os.environ.get(name, "") for name in (*S3_VARIABLES, "S3_REGION")}
    if not all(values[name] for name in S3_VARIABLES):
        return None
    return {name: value for name, value in values.items() if value}


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if "tests/e2e" in str(item.fspath).replace("\\", "/"):
            item.add_marker(pytest.mark.e2e)


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_for(url: str, *, seconds: float = 30) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            if httpx.get(url, timeout=1).status_code < 500:
                return
        except httpx.HTTPError:
            time.sleep(0.2)
    msg = f"{url} ne répond pas après {seconds} s"
    raise RuntimeError(msg)


def stop(process: subprocess.Popen[bytes]) -> None:
    """Arrête un service lancé par les tests : poliment d'abord, puis de force (LiveKit attend
    la fin des connexions ouvertes avant de s'éteindre)."""
    process.terminate()
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)


def find_livekit() -> str | None:
    candidates = [
        os.environ.get("LIVEKIT_SERVER_BIN"),
        shutil.which("livekit-server"),
        str(ROOT / "deploy" / "dev" / "livekit-server.exe"),
        str(ROOT / "deploy" / "dev" / "livekit-server"),
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    return None


@pytest.fixture(scope="session")
def backend_port(request: pytest.FixtureRequest) -> int:
    """Port du backend, choisi avant LiveKit : LiveKit lui envoie ses webhooks (présence)."""
    return DOCKER_BACKEND_PORT if use_docker(request.config) else free_port()


@dataclass
class Receiver:
    """Faux backend d'Eduvia : reçoit les webhooks sortants et les garde, signature comprise."""

    url: str
    calls: list[tuple[dict[str, str], bytes]]
    lock: threading.Lock

    def events(self) -> list[dict[str, Any]]:
        with self.lock:
            return [json.loads(body) for _, body in self.calls]

    def valid_signatures(self) -> bool:
        from visio.services.webhooks import verify_signature

        secret = valid_env()["WEBHOOK_SECRET"]
        with self.lock:
            return bool(self.calls) and all(
                verify_signature(
                    secret,
                    body,
                    timestamp=headers["x-visio-timestamp"],
                    signature=headers["x-visio-signature"],
                )
                for headers, body in self.calls
            )


@pytest.fixture(scope="session")
def receiver() -> Iterator[Receiver]:
    calls: list[tuple[dict[str, str], bytes]] = []
    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802 - nom imposé par http.server
            body = self.rfile.read(int(self.headers.get("content-length", 0)))
            with lock:
                calls.append(({k.lower(): v for k, v in self.headers.items()}, body))
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args: Any) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield Receiver(f"http://127.0.0.1:{server.server_address[1]}/webhooks", calls, lock)
    finally:
        server.shutdown()


@pytest.fixture(scope="session")
def livekit_url(backend_port: int, request: pytest.FixtureRequest) -> Iterator[str | None]:
    if use_docker(request.config):
        livekit_credentials(request.config)
        try:
            wait_for(DOCKER_LIVEKIT_URL.replace("ws://", "http://"), seconds=5)
        except RuntimeError:
            msg = "LiveKit (Docker) ne répond pas sur le port 7880 : lance docker-dev.ps1 up"
            raise RuntimeError(msg) from None
        yield DOCKER_LIVEKIT_URL
        return
    binary = find_livekit()
    if binary is None:
        yield None
        return
    port, tcp_port, udp_start = free_port(), free_port(), 52000
    config = (
        f"port: {port}\nbind_addresses: ['127.0.0.1']\n"
        f"rtc:\n  tcp_port: {tcp_port}\n  port_range_start: {udp_start}\n"
        f"  port_range_end: {udp_start + 200}\n  node_ip: 127.0.0.1\n  use_external_ip: false\n"
        f"keys:\n  {LIVEKIT_KEY}: {LIVEKIT_SECRET}\n"
        f"webhook:\n  api_key: {LIVEKIT_KEY}\n"
        f"  urls:\n    - http://127.0.0.1:{backend_port}/v1/livekit/webhook\n"
    )
    process = subprocess.Popen(
        [binary, "--config-body", config],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        wait_for(f"http://127.0.0.1:{port}")
        yield f"ws://127.0.0.1:{port}"
    finally:
        stop(process)


@dataclass
class Backend:
    base_url: str
    db_url: str
    has_livekit: bool
    receiver: Receiver
    docker: bool = False  # LiveKit, Egress et S3 réels (e2e-docker) ?
    s3: dict[str, str] | None = None  # réglages S3 réels, en mode Docker seulement

    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {SECRET_API_KEY}"}

    def create_meeting(
        self, *, starts_in: float = -60, lasts: float = 3600, **options: Any
    ) -> dict[str, Any]:
        """Crée une réunion par l'API (comme le fera Eduvia). `starts_in` en secondes (négatif :
        elle a déjà commencé)."""
        start = datetime.now(PARIS) + timedelta(seconds=starts_in)
        body: dict[str, Any] = {
            "title": "Cours BTS MCO – Groupe A",
            "starts_at": start.isoformat(timespec="seconds"),
            "ends_at": (start + timedelta(seconds=lasts)).isoformat(timespec="seconds"),
            "organizers": [{"display_name": "Marie Durand", "external_id": "staff_42"}],
            **options,
        }
        response = httpx.post(
            f"{self.base_url}/v1/meetings", json=body, headers=self.headers(), timeout=10
        )
        assert response.status_code == 201, response.text
        created: dict[str, Any] = response.json()
        return created

    def create_link(self, meeting_id: str, **body: Any) -> str:
        response = httpx.post(
            f"{self.base_url}/v1/meetings/{meeting_id}/links",
            json=body,
            headers=self.headers(),
            timeout=10,
        )
        assert response.status_code == 201, response.text
        return str(response.json()["url"])

    def get(self, path: str) -> Any:
        response = httpx.get(f"{self.base_url}{path}", headers=self.headers(), timeout=10)
        assert response.status_code == 200, response.text
        return response.json()

    def end_meeting(self, meeting_id: str) -> None:
        response = httpx.post(
            f"{self.base_url}/v1/meetings/{meeting_id}/end", headers=self.headers(), timeout=10
        )
        assert response.status_code == 200, response.text


@pytest.fixture(scope="session")
def backend(
    tmp_path_factory: pytest.TempPathFactory,
    livekit_url: str | None,
    backend_port: int,
    receiver: Receiver,
    request: pytest.FixtureRequest,
) -> Iterator[Backend]:
    if not (DIST / "index.html").is_file():
        pytest.skip("interface non construite : npm --prefix web run build")
    port = backend_port
    base_url = f"http://127.0.0.1:{port}"
    docker = use_docker(request.config)
    livekit_key, livekit_secret = livekit_credentials(request.config)
    if docker:
        # Le port du webhook est fixe : un autre programme dessus fausserait tout le test.
        with socket.socket() as probe:
            if probe.connect_ex(("127.0.0.1", port)) == 0:
                msg = f"le port {port} est occupé : arrête  uv run task dev-lan  avant e2e-docker"
                raise RuntimeError(msg)
    db_url = f"sqlite:///{tmp_path_factory.mktemp('e2e') / 'visio.db'}"
    upgrade(db_url)
    env = {
        **os.environ,
        **valid_env("development"),
        "DATABASE_URL": db_url,
        "PUBLIC_BASE_URL": base_url,
        "LIVEKIT_URL": livekit_url or "ws://127.0.0.1:1",
        "LIVEKIT_API_KEY": livekit_key,
        "LIVEKIT_API_SECRET": livekit_secret,
        "WEB_DIST_DIR": str(DIST),
        "WEBHOOK_URL": receiver.url,
        "BRAND_NAME": "Académie Test",
        "BRAND_PRIMARY_COLOR": "#2A9D8F",
        "BRAND_SECONDARY_COLOR": "#14213D",
    }
    real_s3 = docker_s3_env() if docker else None
    if real_s3 is not None:
        env.update(real_s3)
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "visio.main:app",
            "--host",
            "0.0.0.0" if docker else "127.0.0.1",  # Docker joint le backend via l'hôte
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        env=env,
        cwd=ROOT,
    )
    try:
        wait_for(f"{base_url}/health")
        yield Backend(
            base_url, db_url, livekit_url is not None, receiver, docker=docker, s3=real_s3
        )
    finally:
        stop(process)


@pytest.fixture
def needs_livekit(backend: Backend) -> None:
    if not backend.has_livekit:
        pytest.skip("binaire LiveKit introuvable (voir tests/e2e/conftest.py)")


@pytest.fixture
def needs_egress(backend: Backend) -> None:
    """Enregistrement réel : Egress et le S3 de dev ne tournent que dans le Docker de dev."""
    if not backend.docker:
        pytest.skip("Egress absent : lance  uv run task e2e-docker  (docker-dev.ps1 up)")
    if backend.s3 is None:
        pytest.skip("réglages S3 absents de l'environnement : lance  uv run task e2e-docker")


BROWSER_ARGS = [
    "--use-fake-ui-for-media-stream",  # accepte micro et caméra sans fenêtre
    "--use-fake-device-for-media-stream",  # micro et caméra simulés
    "--autoplay-policy=no-user-gesture-required",
]


def launch_browser(playwright: Any) -> Any:
    """Le navigateur de test : celui de Playwright s'il est installé, sinon Edge, sinon Chrome
    (déjà présents sur la machine : rien à télécharger). `E2E_BROWSER` impose un choix."""
    imposed = os.environ.get("E2E_BROWSER")
    problems: list[str] = []
    for name in [imposed] if imposed else ["chromium", "msedge", "chrome"]:
        try:
            if name == "chromium":
                return playwright.chromium.launch(args=BROWSER_ARGS)
            return playwright.chromium.launch(channel=name, args=BROWSER_ARGS)
        except Exception as error:  # noqa: BLE001 - on essaie le suivant, puis on explique
            problems.append(f"{name} : {str(error).splitlines()[0]}")
    msg = (
        "aucun navigateur utilisable pour les tests E2E ("
        + " ; ".join(problems)
        + "). Installe Edge ou Chrome, ou lance `uv run playwright install chromium`."
    )
    raise RuntimeError(msg)


@pytest.fixture(scope="session")
def browser() -> Iterator[Any]:
    playwright_api = pytest.importorskip("playwright.sync_api")
    with playwright_api.sync_playwright() as playwright:
        chosen = launch_browser(playwright)
        yield chosen
        chosen.close()


NewPage = Callable[..., Any]


@pytest.fixture
def new_page(browser: Any) -> Iterator[NewPage]:
    """Ouvre un navigateur indépendant (autre personne). Fermés à la fin du test."""
    contexts: list[Any] = []

    def _open(**context_options: Any) -> Any:
        context = browser.new_context(
            permissions=["microphone", "camera"], locale="fr-FR", **context_options
        )
        contexts.append(context)
        page = context.new_page()
        page.set_default_timeout(15_000)
        return page

    yield _open
    for context in contexts:
        context.close()
