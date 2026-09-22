import json
import logging
from typing import Any

import anthropic

from app.db.models import TargetLevel, UiLanguage
from app.services.llm.base import LLMInvalidResponseError, LLMUnavailableError, RuleFailure
from app.services.llm.prompts import (
    ANALYSIS_JSON_SCHEMA,
    EXERCISE_JSON_SCHEMA,
    build_exercise_system_prompt,
    build_exercise_user_message,
    build_system_prompt,
    build_user_message,
)

logger = logging.getLogger(__name__)


class AnthropicClient:
    """Talks to Claude, forcing a JSON answer that matches a given schema."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        timeout_seconds: float,
        max_tokens: int,
        client: anthropic.AsyncAnthropic | None = None,
    ) -> None:
        self.model_name = model
        self._max_tokens = max_tokens
        # The SDK itself retries connection errors, 408, 409, 429 and 5xx with exponential
        # backoff (max_retries), which is exactly "retry only transient errors".
        self._client = client or anthropic.AsyncAnthropic(
            api_key=api_key, timeout=timeout_seconds, max_retries=2
        )

    async def analyze_text(
        self,
        *,
        text: str,
        ui_language: UiLanguage,
        target_level: TargetLevel | None,
    ) -> dict[str, Any]:
        return await self._call(
            system=build_system_prompt(ui_language, target_level),
            user_message=build_user_message(text),
            schema=ANALYSIS_JSON_SCHEMA,
        )

    async def generate_exercises(
        self,
        *,
        rule_failures: list[RuleFailure],
        ui_language: UiLanguage,
        count: int,
    ) -> dict[str, Any]:
        return await self._call(
            system=build_exercise_system_prompt(ui_language, count),
            user_message=build_exercise_user_message(rule_failures),
            schema=EXERCISE_JSON_SCHEMA,
        )

    async def _call(
        self, *, system: str, user_message: str, schema: dict[str, Any]
    ) -> dict[str, Any]:
        try:
            response = await self._client.messages.create(
                model=self.model_name,
                max_tokens=self._max_tokens,
                system=system,
                messages=[{"role": "user", "content": user_message}],
                # Structured output: the API constrains the reply to this schema. It works on
                # every model, unlike forcing a tool call, which some models reject.
                output_config={"format": {"type": "json_schema", "schema": schema}},
            )
        except anthropic.APIStatusError as exc:
            # Never log request content: it is the learner's private text.
            logger.error(
                "LLM request failed with status %s (%s)", exc.status_code, type(exc).__name__
            )
            raise LLMUnavailableError from exc
        except anthropic.APIConnectionError as exc:  # includes timeouts
            logger.error("LLM connection failed (%s)", type(exc).__name__)
            raise LLMUnavailableError from exc

        if response.stop_reason in ("refusal", "max_tokens"):
            # A refusal has no usable answer; max_tokens means the JSON was cut off mid-way.
            raise LLMInvalidResponseError(f"stop_reason={response.stop_reason}")

        # With thinking enabled the first block may be a thinking block: take the text block.
        raw = next((block.text for block in response.content if block.type == "text"), None)
        if raw is None:
            raise LLMInvalidResponseError("no text block in the response")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise LLMInvalidResponseError("response is not valid JSON") from exc
        if not isinstance(data, dict):
            raise LLMInvalidResponseError("response JSON is not an object")
        return data
