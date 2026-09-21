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


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # required fields come from the environment
