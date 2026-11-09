import pytest

from tests.conftest import SECRET_API_KEY, valid_env
from visio.config import ConfigurationError, Settings, get_settings


def test_valid_development_config_is_accepted(set_env) -> None:  # type: ignore[no-untyped-def]
    set_env(valid_env("development"))
    settings = get_settings()
    assert settings.app_env == "development"
    assert settings.s3_region == "eu-west-3"
    assert settings.database_url.startswith("sqlite:")


def test_valid_production_config_is_accepted(set_env) -> None:  # type: ignore[no-untyped-def]
    set_env(valid_env("production"))
    assert get_settings().app_env == "production"


def test_missing_variable_is_reported_by_name(set_env) -> None:  # type: ignore[no-untyped-def]
    env = valid_env()
    del env["API_KEY"]
    del env["S3_BUCKET"]
    set_env(env)
    with pytest.raises(ConfigurationError) as error:
        get_settings()
    message = str(error.value)
    assert "API_KEY : variable manquante" in message
    assert "S3_BUCKET : variable manquante" in message


def test_empty_config_lists_every_missing_variable() -> None:
    with pytest.raises(ConfigurationError) as error:
        get_settings()
    message = str(error.value)
    for name in ("APP_ENV", "API_KEY", "WEBHOOK_SECRET", "LIVEKIT_URL", "S3_ENDPOINT"):
        assert name in message


def test_error_message_never_contains_secret_values(set_env) -> None:  # type: ignore[no-untyped-def]
    env = valid_env()
    env["WEBHOOK_SECRET"] = "valeur-secrete-trop-courte"
    env["BRAND_PRIMARY_COLOR"] = "rouge"
    set_env(env)
    with pytest.raises(ConfigurationError) as error:
        get_settings()
    message = str(error.value)
    assert "valeur-secrete-trop-courte" not in message
    assert "rouge" not in message
    assert SECRET_API_KEY not in message
    assert "WEBHOOK_SECRET : doit contenir au moins 32 caractères" in message
    assert "BRAND_PRIMARY_COLOR" in message


def test_short_secret_is_refused(set_env) -> None:  # type: ignore[no-untyped-def]
    env = valid_env()
    env["API_KEY"] = "x" * 31
    set_env(env)
    with pytest.raises(ConfigurationError, match="API_KEY"):
        get_settings()


def test_invalid_color_is_refused(set_env) -> None:  # type: ignore[no-untyped-def]
    env = valid_env()
    env["BRAND_SECONDARY_COLOR"] = "#12"
    set_env(env)
    with pytest.raises(ConfigurationError, match="BRAND_SECONDARY_COLOR"):
        get_settings()


def test_unknown_environment_is_refused(set_env) -> None:  # type: ignore[no-untyped-def]
    env = valid_env()
    env["APP_ENV"] = "staging"
    set_env(env)
    with pytest.raises(ConfigurationError, match="APP_ENV"):
        get_settings()


def test_production_requires_https(set_env) -> None:  # type: ignore[no-untyped-def]
    env = valid_env("production")
    env["WEBHOOK_URL"] = "http://eduvia.test/webhooks"
    env["LIVEKIT_URL"] = "ws://lk.test"
    set_env(env)
    with pytest.raises(ConfigurationError) as error:
        get_settings()
    assert "WEBHOOK_URL doit utiliser https://" in str(error.value)
    assert "LIVEKIT_URL doit utiliser wss://" in str(error.value)


def test_development_allows_http(set_env) -> None:  # type: ignore[no-untyped-def]
    env = valid_env("development")
    env["WEBHOOK_URL"] = "http://localhost:9999/hook"
    set_env(env)
    assert get_settings().webhook_url.startswith("http://")


@pytest.mark.parametrize("name", ["API_KEY", "S3_SECRET_KEY", "S3_BUCKET", "WEBHOOK_URL"])
def test_production_refuses_placeholders(set_env, name: str) -> None:  # type: ignore[no-untyped-def]
    env = valid_env("production")
    env[name] = (
        "CHANGE_ME" + "x" * 40 if name == "API_KEY" else env[name].replace("test", "CHANGE_ME")
    )
    if name == "S3_BUCKET":
        env[name] = "CHANGE_ME"
    set_env(env)
    with pytest.raises(ConfigurationError, match="CHANGE_ME"):
        get_settings()


def test_secrets_are_not_leaked_in_repr(set_env) -> None:  # type: ignore[no-untyped-def]
    set_env(valid_env())
    settings = get_settings()
    assert SECRET_API_KEY not in repr(settings)
    assert SECRET_API_KEY not in str(settings)
    assert settings.api_key.get_secret_value() == SECRET_API_KEY


def test_settings_are_immutable(set_env) -> None:  # type: ignore[no-untyped-def]
    set_env(valid_env())
    settings = get_settings()
    with pytest.raises(Exception):  # noqa: B017
        settings.brand_name = "Autre"


def test_settings_do_not_read_env_files(tmp_path, monkeypatch, set_env) -> None:  # type: ignore[no-untyped-def]
    """Un .env en clair posé dans le dossier courant ne doit jamais être lu."""
    (tmp_path / ".env").write_text("API_KEY=" + "z" * 40 + "\n")
    monkeypatch.chdir(tmp_path)
    set_env(valid_env())
    assert Settings().api_key.get_secret_value() == SECRET_API_KEY  # type: ignore[call-arg]


def test_undecrypted_values_give_a_clear_key_message(set_env) -> None:  # type: ignore[no-untyped-def]
    env = valid_env()
    env["API_KEY"] = "encrypted:BDaBK6SSxw1YkhVeb5B"
    set_env(env)
    with pytest.raises(ConfigurationError) as error:
        get_settings()
    message = str(error.value)
    assert "DOTENV_PRIVATE_KEY" in message
    assert "API_KEY" in message
    assert "BDaBK6SSxw1YkhVeb5B" not in message


def test_recording_template_url_is_required(set_env) -> None:  # type: ignore[no-untyped-def]
    env = valid_env()
    del env["RECORDING_TEMPLATE_URL"]
    set_env(env)
    with pytest.raises(ConfigurationError) as error:
        get_settings()
    assert "RECORDING_TEMPLATE_URL : variable manquante" in str(error.value)


def test_recording_template_url_must_be_http(set_env) -> None:  # type: ignore[no-untyped-def]
    env = valid_env()
    env["RECORDING_TEMPLATE_URL"] = "ftp://visio.test/recording-template"
    set_env(env)
    with pytest.raises(ConfigurationError) as error:
        get_settings()
    assert "RECORDING_TEMPLATE_URL" in str(error.value)
