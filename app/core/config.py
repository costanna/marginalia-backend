from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

PLACEHOLDER_SECRET_PREFIX = "change-me"


class Settings(BaseSettings):
    """Application settings, read from environment variables (and a local .env file)."""

    # extra="ignore": .env also holds POSTGRES_* variables used only by docker-compose.
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: Literal["development", "test", "production"] = "development"
    database_url: str
    secret_key: str = Field(min_length=32)
    access_token_expire_minutes: int = Field(default=60, gt=0)
    # NoDecode: read the raw string ("a,b") instead of expecting JSON, then split it below.
    cors_origins: Annotated[list[str], NoDecode] = ["http://localhost:4200"]

    # --- LLM ---
    # fake: deterministic offline client (development, tests; costs nothing).
    # openai_compatible: any provider speaking the OpenAI chat protocol, including free tiers
    #   (Groq, Gemini, OpenRouter, ...); needs LLM_BASE_URL, LLM_API_KEY and LLM_MODEL.
    # anthropic: the Anthropic API (paid; optional).
    llm_provider: Literal["fake", "openai_compatible", "anthropic"] = "fake"
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = ""
    llm_timeout_seconds: float = Field(default=30, gt=0)
    # Upper bound for one analysis response; keeps a runaway generation from costing too much.
    llm_max_tokens: int = Field(default=4096, gt=0)

    # --- Limits ---
    max_text_chars: int = Field(default=3000, gt=0)
    daily_analysis_limit: int = Field(default=10, gt=0)
    daily_generation_limit: int = Field(default=5, gt=0)
    demo_daily_limit: int = Field(default=3, gt=0)
    # Number of reverse proxies in front of the API that append to X-Forwarded-For (0 = none).
    # Needed to see the visitor's IP instead of the proxy's (see app/core/rate_limit.py).
    trusted_proxy_hops: int = Field(default=0, ge=0)
    # Turns on GET /api/v1/diagnostics/client-ip (off by default). Meant to be enabled for a few
    # minutes after a deployment to see which headers the host's proxies really send.
    enable_diagnostics: bool = False

    @field_validator("database_url")
    @classmethod
    def name_the_psycopg_driver(cls, value: str) -> str:
        """Accept the URL exactly as the host hands it out.

        Neon and most hosts give `postgresql://...` (or the older `postgres://...`), but SQLAlchemy
        must be told to use the psycopg 3 driver. Converting here removes a classic deployment
        mistake: pasting the connection string as-is and getting "No module named psycopg2".
        """
        for prefix in ("postgres://", "postgresql://"):
            if value.startswith(prefix):
                return "postgresql+psycopg://" + value[len(prefix) :]
        return value

    @field_validator("cors_origins", mode="before")
    @classmethod
    def split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @model_validator(mode="after")
    def reject_placeholder_secret_in_production(self) -> "Settings":
        if self.environment == "production" and self.secret_key.startswith(
            PLACEHOLDER_SECRET_PREFIX
        ):
            raise ValueError("SECRET_KEY must be replaced with a random value in production")
        return self

    @model_validator(mode="after")
    def require_credentials_for_a_real_llm(self) -> "Settings":
        required = {
            "anthropic": {"LLM_API_KEY": self.llm_api_key, "LLM_MODEL": self.llm_model},
            "openai_compatible": {
                "LLM_BASE_URL": self.llm_base_url,
                "LLM_API_KEY": self.llm_api_key,
                "LLM_MODEL": self.llm_model,
            },
        }.get(self.llm_provider, {})
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise ValueError(f"{', '.join(missing)} required when LLM_PROVIDER={self.llm_provider}")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # required fields come from the environment
