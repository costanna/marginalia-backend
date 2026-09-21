import pytest
from pydantic import ValidationError

from app.core.config import Settings

VALID_SECRET = "x" * 40
DB_URL = "postgresql+psycopg://u:p@localhost:5432/db"


def make_settings(**overrides: object) -> Settings:
    # _env_file=None: ignore any local .env so the test only sees what it passes in.
    values = {"database_url": DB_URL, "secret_key": VALID_SECRET, **overrides}
    return Settings(_env_file=None, **values)  # type: ignore[call-arg, arg-type]


def test_cors_origins_are_parsed_from_a_comma_separated_string() -> None:
    settings = make_settings(cors_origins="https://app.vercel.app, http://localhost:4200,")

    assert settings.cors_origins == ["https://app.vercel.app", "http://localhost:4200"]


def test_secret_key_must_be_long_enough() -> None:
    with pytest.raises(ValidationError):
        make_settings(secret_key="too-short")


def test_placeholder_secret_is_rejected_in_production_only() -> None:
    placeholder = "change-me-" + "x" * 40

    assert make_settings(secret_key=placeholder, environment="development")
    with pytest.raises(ValidationError):
        make_settings(secret_key=placeholder, environment="production")


def test_defaults() -> None:
    settings = make_settings()

    assert settings.access_token_expire_minutes == 60
    assert settings.cors_origins == ["http://localhost:4200"]
