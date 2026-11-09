from fastapi.testclient import TestClient

from visio.main import create_app


def test_health_returns_ok() -> None:
    client = TestClient(create_app())
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_unknown_route_returns_404_not_an_empty_room() -> None:
    client = TestClient(create_app())
    assert client.get("/une-url-inventee").status_code == 404


def test_openapi_is_exposed() -> None:
    client = TestClient(create_app())
    spec = client.get("/openapi.json").json()
    assert spec["info"]["title"] == "Eduvia Visio"
    assert "/health" in spec["paths"]
