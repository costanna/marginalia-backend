import importlib
import re
from pathlib import Path

ENV_EXAMPLE = Path(__file__).resolve().parent.parent / ".env.example"

# Variables the deployment table (spec section 17.2) requires.
REQUIRED_ENV_VARS = {
    "DATABASE_URL",
    "SECRET_KEY",
    "ACCESS_TOKEN_EXPIRE_MINUTES",
    "CORS_ORIGINS",
    "LLM_PROVIDER",
    "LLM_API_KEY",
    "LLM_MODEL",
    "LLM_TIMEOUT_SECONDS",
    "DAILY_ANALYSIS_LIMIT",
    "DEMO_DAILY_LIMIT",
    "MAX_TEXT_CHARS",
    "ENVIRONMENT",
}


def test_app_package_is_importable() -> None:
    assert importlib.import_module("app") is not None


def test_env_example_documents_every_required_variable() -> None:
    declared = set(re.findall(r"^([A-Z_]+)=", ENV_EXAMPLE.read_text(), flags=re.MULTILINE))
    missing = REQUIRED_ENV_VARS - declared
    assert not missing, f"missing from .env.example: {sorted(missing)}"


def test_env_example_contains_no_real_secrets() -> None:
    content = ENV_EXAMPLE.read_text()
    assert re.search(r"^LLM_API_KEY=$", content, flags=re.MULTILINE)
    assert re.search(r"^SECRET_KEY=change-me", content, flags=re.MULTILINE)
