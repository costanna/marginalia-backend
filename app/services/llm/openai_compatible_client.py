"""Client for any provider that speaks the widely copied OpenAI `chat/completions` protocol.

Groq, Google Gemini (OpenAI-compatible endpoint), OpenRouter, Mistral, Together, a local Ollama...
all accept it, and several have a FREE tier, which is what lets this portfolio project run at zero
cost: switching provider is a change of three environment variables, not of code.
"""

import asyncio
import json
import logging
import re
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from app.db.models import TargetLevel, UiLanguage
from app.services.llm.base import LLMInvalidResponseError, LLMUnavailableError, RuleFailure
from app.services.llm.prompts import (
    JSON_SHAPE_HINT,
    JSON_SHAPE_HINT_EXERCISES,
    build_exercise_system_prompt,
    build_exercise_user_message,
    build_system_prompt,
    build_user_message,
)

logger = logging.getLogger(__name__)

# Rate limits (429), timeouts (408) and server errors are transient: worth another try.
RETRYABLE_STATUS = {408, 429}
MAX_BACKOFF_SECONDS = 10.0

_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL | re.IGNORECASE)


def _retryable(status_code: int) -> bool:
    return status_code in RETRYABLE_STATUS or status_code >= 500


def _parse_json_object(content: str) -> dict[str, Any]:
    """Extract the JSON object from the model's text.

    JSON mode makes most models answer with bare JSON, but some still wrap it in a markdown fence
    or add a sentence around it; being lenient here saves a retry (and, on a free tier, a request).
    """
    text = content.strip()
    fenced = _FENCE.match(text)
    if fenced:
        text = fenced.group(1)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise LLMInvalidResponseError("response is not valid JSON") from None
        try:
            data = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            raise LLMInvalidResponseError("response is not valid JSON") from None
    if not isinstance(data, dict):
        raise LLMInvalidResponseError("response JSON is not an object")
    return data


class OpenAICompatibleClient:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout_seconds: float,
        max_tokens: int,
        max_retries: int = 2,
        http_client: httpx.AsyncClient | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.model_name = model
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._api_key = api_key
        self._max_tokens = max_tokens
        self._max_retries = max_retries
        self._sleep = sleep
        self._http = http_client or httpx.AsyncClient(timeout=timeout_seconds)

    async def analyze_text(
        self,
        *,
        text: str,
        ui_language: UiLanguage,
        target_level: TargetLevel | None,
    ) -> dict[str, Any]:
        system_prompt = build_system_prompt(ui_language, target_level) + JSON_SHAPE_HINT
        payload = {
            "model": self.model_name,
            "max_tokens": self._max_tokens,
            "temperature": 0.2,  # low: corrections should be consistent, not creative
            # JSON mode is supported by far more providers than strict JSON-Schema mode; the shape
            # is described in the prompt and enforced by our own validation.
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": build_user_message(text)},
            ],
        }
        response = await self._post_with_retries(payload)
        return self._extract_answer(response)

    async def generate_exercises(
        self,
        *,
        rule_failures: list[RuleFailure],
        ui_language: UiLanguage,
        count: int,
    ) -> dict[str, Any]:
        system_prompt = build_exercise_system_prompt(ui_language, count) + JSON_SHAPE_HINT_EXERCISES
        payload = {
            "model": self.model_name,
            "max_tokens": self._max_tokens,
            "temperature": 0.4,  # a little higher than analysis: fresh sentences, not one answer
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": build_exercise_user_message(rule_failures)},
            ],
        }
        response = await self._post_with_retries(payload)
        return self._extract_answer(response)

    async def _post_with_retries(self, payload: dict[str, Any]) -> httpx.Response:
        headers = {"Authorization": f"Bearer {self._api_key}"}
        for attempt in range(self._max_retries + 1):
            is_last = attempt == self._max_retries
            try:
                response = await self._http.post(self._url, json=payload, headers=headers)
            except httpx.TransportError as exc:  # connection problems and timeouts
                # Never log the request: it holds the learner's private text and the API key.
                logger.error("LLM connection failed (%s)", type(exc).__name__)
                if is_last:
                    raise LLMUnavailableError from exc
                await self._sleep(self._backoff(attempt, None))
                continue

            if response.status_code < 400:
                return response
            logger.error("LLM request failed with status %s", response.status_code)
            if is_last or not _retryable(response.status_code):
                raise LLMUnavailableError(f"status {response.status_code}")
            await self._sleep(self._backoff(attempt, response.headers.get("retry-after")))
        raise LLMUnavailableError  # unreachable: the last attempt always returns or raises

    @staticmethod
    def _backoff(attempt: int, retry_after: str | None) -> float:
        """Exponentially growing wait (1s, 2s, ...), or what the provider asked for, capped."""
        if retry_after is not None:
            try:
                return min(max(float(retry_after), 0.0), MAX_BACKOFF_SECONDS)
            except ValueError:
                pass
        return min(float(2**attempt), MAX_BACKOFF_SECONDS)

    @staticmethod
    def _extract_answer(response: httpx.Response) -> dict[str, Any]:
        try:
            choice = response.json()["choices"][0]
            content = choice["message"]["content"]
            finish_reason = choice.get("finish_reason")
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise LLMInvalidResponseError("unexpected response shape") from exc
        if finish_reason == "length":
            raise LLMInvalidResponseError("response was cut off (max_tokens)")
        if not isinstance(content, str) or not content.strip():
            raise LLMInvalidResponseError("empty answer")
        return _parse_json_object(content)
