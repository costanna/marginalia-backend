import pytest
from pydantic import ValidationError

from app.core.config import MAX_REQUEST_TEXT_CHARS, Settings

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


def test_token_lifetime_and_llm_timeout_have_upper_bounds() -> None:
    """A typo must fail at startup: a near-immortal token has no revocation list behind it, and
    an unbounded provider timeout would let one stuck call drain the whole worker pool."""
    assert make_settings(access_token_expire_minutes=1440)
    assert make_settings(llm_timeout_seconds=120)
    with pytest.raises(ValidationError):
        make_settings(access_token_expire_minutes=1441)
    with pytest.raises(ValidationError):
        make_settings(llm_timeout_seconds=121)


def test_wildcard_cors_is_rejected_in_production_only() -> None:
    assert make_settings(cors_origins="*", environment="development")
    with pytest.raises(ValidationError):
        make_settings(cors_origins="*", environment="production")
    with pytest.raises(ValidationError):
        make_settings(cors_origins="https://app.vercel.app, *", environment="production")


def test_max_text_chars_cannot_exceed_the_request_body_cap() -> None:
    """Otherwise the schema would reject a text the app itself allows, and as a generic
    `validation_error` instead of the translated `text_too_long` (see MAX_REQUEST_TEXT_CHARS)."""
    assert make_settings(max_text_chars=MAX_REQUEST_TEXT_CHARS)
    with pytest.raises(ValidationError):
        make_settings(max_text_chars=MAX_REQUEST_TEXT_CHARS + 1)


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        # exactly as Neon shows it, with its query parameters
        (
            "postgresql://u:p@ep-x.eu-central-1.aws.neon.tech/neondb?sslmode=require",
            "postgresql+psycopg://u:p@ep-x.eu-central-1.aws.neon.tech/neondb?sslmode=require",
        ),
        ("postgres://u:p@host/db", "postgresql+psycopg://u:p@host/db"),  # older scheme name
        ("postgresql+psycopg://u:p@host/db", "postgresql+psycopg://u:p@host/db"),  # already right
    ],
)
def test_database_url_gets_the_psycopg_driver_when_it_is_missing(given: str, expected: str) -> None:
    assert make_settings(database_url=given).database_url == expected
