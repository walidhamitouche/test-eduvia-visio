from collections.abc import Iterator

import pytest

from visio.config import get_settings

CONFIG_VARS = (
    "APP_ENV",
    "API_KEY",
    "LINK_SIGNING_SECRET",
    "PUBLIC_BASE_URL",
    "WEBHOOK_URL",
    "WEBHOOK_SECRET",
    "LIVEKIT_URL",
    "LIVEKIT_API_KEY",
    "LIVEKIT_API_SECRET",
    "S3_ENDPOINT",
    "S3_BUCKET",
    "S3_ACCESS_KEY",
    "S3_SECRET_KEY",
    "S3_REGION",
    "BRAND_NAME",
    "BRAND_LOGO_URL",
    "BRAND_PRIMARY_COLOR",
    "BRAND_SECONDARY_COLOR",
    "DATABASE_URL",
    "WEB_DIST_DIR",
    "SCHEDULER_INTERVAL_SECONDS",
    "RECORDING_TEMPLATE_URL",
    "RECORDING_SEGMENT_SECONDS",
    "LOG_LEVEL",
    "LOG_FORMAT",
)

SECRET_API_KEY = "test-api-key-" + "a" * 40


def valid_env(app_env: str = "development") -> dict[str, str]:
    """Une configuration complète et valide, utilisée uniquement par les tests."""
    https = app_env == "production"
    http = "https" if https else "http"
    return {
        "APP_ENV": app_env,
        "API_KEY": SECRET_API_KEY,
        "LINK_SIGNING_SECRET": "test-link-secret-" + "b" * 40,
        "PUBLIC_BASE_URL": f"{http}://visio.test",
        "WEBHOOK_URL": f"{http}://eduvia.test/webhooks/visio",
        "WEBHOOK_SECRET": "test-webhook-secret-" + "c" * 40,
        "LIVEKIT_URL": "wss://lk.test" if https else "ws://localhost:7880",
        "LIVEKIT_API_KEY": "lk-key",
        "LIVEKIT_API_SECRET": "test-livekit-secret-" + "d" * 40,
        "S3_ENDPOINT": f"{http}://s3.test",
        "S3_BUCKET": "visio-test",
        "S3_ACCESS_KEY": "s3-access",
        "S3_SECRET_KEY": "test-s3-secret-" + "e" * 40,
        "BRAND_NAME": "Test Visio",
        "BRAND_LOGO_URL": f"{http}://visio.test/logo.svg",
        "BRAND_PRIMARY_COLOR": "#E8505B",
        "BRAND_SECONDARY_COLOR": "#1F1F1F",
        "RECORDING_TEMPLATE_URL": f"{http}://visio.test/recording-template",
    }


@pytest.fixture(autouse=True)
def clean_config_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Isole chaque test de l'environnement réel de la machine."""
    for name in CONFIG_VARS:
        monkeypatch.delenv(name, raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def set_env(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    def _set(values: dict[str, str]) -> None:
        for key, value in values.items():
            monkeypatch.setenv(key, value)

    return _set
