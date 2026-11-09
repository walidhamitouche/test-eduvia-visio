from visio.tools.envcheck import check_env_file_text, is_keys_file
from visio.tools.envinit import build_values, render

ENCRYPTED = (
    'DOTENV_PUBLIC_KEY_DEVELOPMENT="02dd78"\n'
    "# .env.development\n"
    "API_KEY=encrypted:BDaBK6SSxw1YkhVeb5B\n"
    "APP_ENV=encrypted:BFQoEW7Yrug\n"
)


def test_fully_encrypted_file_is_clean() -> None:
    assert check_env_file_text(".env.development", ENCRYPTED) == []


def test_clear_value_is_detected_without_echoing_it() -> None:
    problems = check_env_file_text(".env.production", "API_KEY=super-secret-value\n")
    assert problems == [".env.production:1 valeur en clair pour API_KEY"]
    assert "super-secret-value" not in problems[0]


def test_private_key_is_detected() -> None:
    problems = check_env_file_text(".env.production", "DOTENV_PRIVATE_KEY_PRODUCTION=abcdef\n")
    assert len(problems) == 1
    assert "clé privée" in problems[0]
    assert "abcdef" not in problems[0]


def test_comments_blank_lines_and_quotes_are_handled() -> None:
    text = '\n# commentaire\nexport API_KEY="encrypted:xyz"\n'
    assert check_env_file_text(".env.test", text) == []


def test_generated_values_are_random_and_complete() -> None:
    first, second = build_values("development"), build_values("development")
    assert first["API_KEY"] != second["API_KEY"]
    assert len(first["API_KEY"]) >= 32
    assert first["APP_ENV"] == "development"
    rendered = render(first)
    assert 'API_KEY="' in rendered
    assert 'BRAND_PRIMARY_COLOR="#E8505B"' in rendered


def test_production_template_uses_https_and_placeholders() -> None:
    values = build_values("production")
    assert values["PUBLIC_BASE_URL"].startswith("https://")
    assert values["S3_SECRET_KEY"] == "CHANGE_ME"


def test_generated_development_values_satisfy_the_schema(set_env) -> None:  # type: ignore[no-untyped-def]
    from visio.config import get_settings

    values = build_values("development")
    set_env(values)
    assert get_settings().app_env == "development"


def test_keys_files_and_their_copies_are_recognised() -> None:
    assert is_keys_file(".env.keys")
    assert is_keys_file(".env.keys.bak")
    assert is_keys_file("sous/dossier/.env.keys.old")
    assert not is_keys_file(".env.production")
    assert not is_keys_file(".env.example")
