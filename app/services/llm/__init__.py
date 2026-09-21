from functools import lru_cache

from app.core.config import get_settings
from app.services.llm.anthropic_client import AnthropicClient
from app.services.llm.base import LLMClient
from app.services.llm.fake_client import FakeLLMClient


@lru_cache
def get_llm_client() -> LLMClient:
    """Pick the provider from LLM_PROVIDER. Also the FastAPI dependency (overridden in tests)."""
    settings = get_settings()
    if settings.llm_provider == "anthropic":
        return AnthropicClient(
            api_key=settings.llm_api_key,
            model=settings.llm_model,
            timeout_seconds=settings.llm_timeout_seconds,
            max_tokens=settings.llm_max_tokens,
        )
    return FakeLLMClient()
