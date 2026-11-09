"""Interface de visio servie par le backend (étape 7) : page `/j/<jeton>`, marque blanche,
fichiers construits. Un faux `web/dist` est fabriqué dans un dossier temporaire."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.conftest import valid_env
from visio.api.web import PLACEHOLDER, readable_on
from visio.config import Settings
from visio.db.migrate import upgrade
from visio.main import create_app

INDEX = (
    f"<!doctype html><html lang='fr'><head>{PLACEHOLDER}</head>"
    "<body><div id='root'></div></body></html>"
)


@pytest.fixture(autouse=True)
def dist(tmp_path: Path, set_env: Callable[[dict[str, str]], None]) -> Path:
    """Posé avant `settings` (autouse) : `WEB_DIST_DIR` est lu au moment de créer la config."""
    folder = tmp_path / "dist"
    (folder / "assets").mkdir(parents=True)
    (folder / "index.html").write_text(INDEX, encoding="utf-8")
    (folder / "assets" / "app-abc123.js").write_text("export const x = 1;", encoding="utf-8")
    (folder / "assets" / "app-abc123.css").write_text("body{margin:0}", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("hors du dossier assets", encoding="utf-8")
    set_env({"WEB_DIST_DIR": str(folder)})
    return folder


def brand_of(page: str) -> dict[str, Any]:
    match = re.search(r'<script id="brand-config" type="application/json">(.*?)</script>', page)
    assert match, page
    config: dict[str, Any] = json.loads(match.group(1))
    return config


def test_any_link_gets_the_same_page_valid_or_not(client: TestClient, create: Any) -> None:
    valid = client.get("/j/" + create()["participant_url"].rsplit("/j/", 1)[1])
    invented = client.get("/j/jeton-invente")
    assert valid.status_code == invented.status_code == 200
    assert valid.text == invented.text  # la page ne révèle pas si un lien est valide


def test_brand_comes_from_the_configuration(client: TestClient) -> None:
    page = client.get("/j/x").text
    assert "<title>Test Visio</title>" in page
    assert "--brand-primary:#E8505B" in page
    assert "--brand-secondary:#1F1F1F" in page
    assert "--brand-on-secondary:#FFFFFF" in page
    assert brand_of(page) == {"name": "Test Visio", "logo_url": "http://visio.test/logo.svg"}
    assert PLACEHOLDER not in page


def test_brand_values_are_escaped(
    set_env: Callable[[dict[str, str]], None], tmp_path: Path
) -> None:
    db_url = f"sqlite:///{tmp_path / 'brand.db'}"
    hostile = "</script><script>alert(1)</script> & <b>"
    set_env({**valid_env(), "DATABASE_URL": db_url, "BRAND_NAME": hostile})
    upgrade(db_url)
    with TestClient(create_app(settings=Settings())) as http_client:  # type: ignore[call-arg]
        page = http_client.get("/j/x").text
    assert "<script>alert(1)" not in page
    assert page.count("<script") == 1  # seulement la balise de configuration
    assert "&lt;/script&gt;" in page  # titre échappé en HTML
    assert brand_of(page)["name"] == hostile  # et la configuration relue à l'identique


def test_page_is_never_cached_and_sends_no_referrer(client: TestClient) -> None:
    headers = client.get("/j/x").headers
    assert headers["cache-control"] == "no-store"
    assert headers["referrer-policy"] == "no-referrer"
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["content-type"].startswith("text/html")


def test_missing_build_gives_a_clear_error(client: TestClient, dist: Path) -> None:
    (dist / "index.html").unlink()
    response = client.get("/j/x")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "web_not_built"
    assert "npm" in response.json()["error"]["message"]


def test_assets_are_served_with_the_right_type_and_long_cache(client: TestClient) -> None:
    script = client.get("/assets/app-abc123.js")
    assert script.status_code == 200
    assert script.headers["content-type"].startswith("text/javascript")
    assert "immutable" in script.headers["cache-control"]
    assert client.get("/assets/app-abc123.css").headers["content-type"].startswith("text/css")


@pytest.mark.parametrize(
    "path",
    [
        "/assets/absent.js",
        "/assets/../secret.txt",
        "/assets/..%2Fsecret.txt",
        "/assets/%2e%2e/secret.txt",
    ],
)
def test_assets_cannot_leave_their_folder(client: TestClient, path: str) -> None:
    response = client.get(path)
    assert response.status_code == 404
    assert "hors du dossier" not in response.text
    assert response.json()["error"]["code"] == "not_found"


def test_index_is_not_reachable_as_an_asset(client: TestClient) -> None:
    assert client.get("/assets/../index.html").status_code == 404


def test_production_refuses_plain_http_for_the_page(
    set_env: Callable[[dict[str, str]], None], tmp_path: Path, dist: Path
) -> None:
    db_url = f"sqlite:///{tmp_path / 'prod.db'}"
    set_env({**valid_env("production"), "DATABASE_URL": db_url})
    upgrade(db_url)
    with TestClient(create_app(settings=Settings())) as http_client:  # type: ignore[call-arg]
        refused = http_client.get("/j/x")
        assert refused.status_code == 403
        assert refused.json()["error"]["code"] == "https_required"
        assert http_client.get("/j/x", headers={"X-Forwarded-Proto": "https"}).status_code == 200


def test_pages_are_not_in_the_openapi_spec(client: TestClient) -> None:
    paths = client.get("/openapi.json").json()["paths"]
    assert not any(p.startswith(("/j/", "/assets")) for p in paths)


@pytest.mark.parametrize(
    ("color", "expected"),
    [
        ("#FFFFFF", "#000000"),
        ("#000000", "#FFFFFF"),
        ("#1F1F1F", "#FFFFFF"),
        ("#E8505B", "#000000"),
        ("#FFD400", "#000000"),
        ("#0B3D91", "#FFFFFF"),
    ],
)
def test_text_color_is_readable_on_the_brand_color(color: str, expected: str) -> None:
    assert readable_on(color) == expected
