import pytest
from fastapi.testclient import TestClient

from tests.conftest import valid_env
from visio.config import ConfigurationError
from visio.main import create_app


def test_app_refuses_to_start_without_configuration() -> None:
    with pytest.raises(ConfigurationError) as error, TestClient(create_app()):
        pass
    assert "API_KEY" in str(error.value)


def test_app_starts_with_valid_configuration(set_env) -> None:  # type: ignore[no-untyped-def]
    set_env(valid_env())
    app = create_app()
    with TestClient(app) as client:
        assert client.get("/health").json() == {"status": "ok"}
        assert app.state.settings.brand_name == "Test Visio"
