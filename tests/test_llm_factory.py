from collections.abc import Iterator
from typing import Any

import pytest
from pydantic import ValidationError

from app.core.config import Settings, get_settings
from app.services.llm import get_llm_client
from app.services.llm.anthropic_client import AnthropicClient
from app.services.llm.fake_client import FakeLLMClient
from app.services.llm.openai_compatible_client import OpenAICompatibleClient


@pytest.fixture(autouse=True)
def fresh_caches() -> Iterator[None]:
    """The factory and the settings are cached: reset them around every test in this module."""
    get_settings.cache_clear()
    get_llm_client.cache_clear()
    yield
    get_settings.cache_clear()
    get_llm_client.cache_clear()


def test_the_fake_client_is_the_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "fake")

    assert isinstance(get_llm_client(), FakeLLMClient)


def test_the_real_client_is_built_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("LLM_API_KEY", "sk-test-not-a-real-key")
    monkeypatch.setenv("LLM_MODEL", "my-model")

    client = get_llm_client()

    assert isinstance(client, AnthropicClient)
    assert client.model_name == "my-model"


def test_an_openai_compatible_client_is_built_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "openai_compatible")
    monkeypatch.setenv("LLM_BASE_URL", "https://llm.example.com/v1")
    monkeypatch.setenv("LLM_API_KEY", "free-tier-key")
    monkeypatch.setenv("LLM_MODEL", "free-model")

    client = get_llm_client()

    assert isinstance(client, OpenAICompatibleClient)
    assert client.model_name == "free-model"


@pytest.mark.parametrize("missing", [{"llm_api_key": ""}, {"llm_model": ""}])
def test_the_anthropic_provider_requires_a_key_and_a_model(missing: dict[str, str]) -> None:
    values: dict[str, Any] = {"llm_api_key": "k", "llm_model": "m", **missing}

    with pytest.raises(ValidationError):
        Settings(  # type: ignore[call-arg]
            _env_file=None,
            database_url="postgresql+psycopg://u:p@localhost/db",
            secret_key="x" * 40,
            llm_provider="anthropic",
            **values,
        )


@pytest.mark.parametrize("missing", ["llm_base_url", "llm_api_key", "llm_model"])
def test_the_openai_compatible_provider_requires_url_key_and_model(missing: str) -> None:
    values: dict[str, Any] = {"llm_base_url": "https://x/v1", "llm_api_key": "k", "llm_model": "m"}
    values[missing] = ""

    with pytest.raises(ValidationError, match=missing.upper()):
        Settings(  # type: ignore[call-arg]
            _env_file=None,
            database_url="postgresql+psycopg://u:p@localhost/db",
            secret_key="x" * 40,
            llm_provider="openai_compatible",
            **values,
        )


def test_the_default_provider_needs_no_credentials() -> None:
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        database_url="postgresql+psycopg://u:p@localhost/db",
        secret_key="x" * 40,
    )

    assert settings.llm_provider == "fake"
