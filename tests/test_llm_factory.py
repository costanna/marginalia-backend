from collections.abc import Iterator
from typing import Any

import pytest
from pydantic import ValidationError

from app.core.config import Settings, get_settings
from app.services.llm import get_llm_client
from app.services.llm.anthropic_client import AnthropicClient
from app.services.llm.fake_client import FakeLLMClient


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


@pytest.mark.parametrize("missing", [{"llm_api_key": ""}, {"llm_model": ""}])
def test_the_real_provider_requires_a_key_and_a_model(missing: dict[str, str]) -> None:
    values: dict[str, Any] = {"llm_api_key": "k", "llm_model": "m", **missing}

    with pytest.raises(ValidationError):
        Settings(  # type: ignore[call-arg]
            _env_file=None,
            database_url="postgresql+psycopg://u:p@localhost/db",
            secret_key="x" * 40,
            llm_provider="anthropic",
            **values,
        )
