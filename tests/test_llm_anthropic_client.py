"""AnthropicClient tests. The real API is never called: a stub stands in for the SDK client."""

import json
from types import SimpleNamespace
from typing import Any, cast

import anthropic
import httpx2
import pytest

from app.db.models import RuleTag, TargetLevel, UiLanguage
from app.services.llm.anthropic_client import AnthropicClient
from app.services.llm.base import LLMInvalidResponseError, LLMUnavailableError, RuleFailure
from app.services.llm.prompts import ANALYSIS_JSON_SCHEMA, EXERCISE_JSON_SCHEMA

ANSWER = {
    "cefr_level": "A2",
    "summary": "Nice.",
    "corrections": [],
}
REQUEST = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")


class StubMessages:
    def __init__(self, response: object = None, error: Exception | None = None) -> None:
        self.response = response
        self.error = error
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> object:
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.response


def reply(*blocks: object, stop_reason: str = "end_turn") -> SimpleNamespace:
    return SimpleNamespace(stop_reason=stop_reason, content=list(blocks))


def text_block(text: str) -> SimpleNamespace:
    return SimpleNamespace(type="text", text=text)


def make_client(messages: StubMessages) -> AnthropicClient:
    stub = cast(anthropic.AsyncAnthropic, SimpleNamespace(messages=messages))
    return AnthropicClient(
        api_key="unused", model="some-model", timeout_seconds=30, max_tokens=1234, client=stub
    )


async def call(client: AnthropicClient, text: str = "Hello world.") -> dict[str, Any]:
    return await client.analyze_text(
        text=text, ui_language=UiLanguage.CA, target_level=TargetLevel.B1
    )


async def test_returns_the_parsed_json() -> None:
    client = make_client(StubMessages(reply(text_block(json.dumps(ANSWER)))))

    assert await call(client) == ANSWER


async def test_the_request_carries_model_limits_schema_and_wrapped_text() -> None:
    messages = StubMessages(reply(text_block(json.dumps(ANSWER))))

    await call(make_client(messages), "My text.")

    request = messages.calls[0]
    assert request["model"] == "some-model"
    assert request["max_tokens"] == 1234
    assert request["output_config"] == {
        "format": {"type": "json_schema", "schema": ANALYSIS_JSON_SCHEMA}
    }
    assert "Catalan" in request["system"]
    assert "aiming for level B1" in request["system"]
    assert request["messages"] == [
        {"role": "user", "content": "<user_text>\nMy text.\n</user_text>"}
    ]
    assert "tool_choice" not in request  # forced tool use is rejected by some models


async def test_skips_thinking_blocks_to_find_the_text() -> None:
    thinking = SimpleNamespace(type="thinking", thinking="...")
    client = make_client(StubMessages(reply(thinking, text_block(json.dumps(ANSWER)))))

    assert await call(client) == ANSWER


@pytest.mark.parametrize(
    "response",
    [
        reply(text_block("not json at all")),
        reply(text_block("[1, 2, 3]")),  # JSON, but not an object
        reply(),  # no content
        reply(text_block("{}"), stop_reason="refusal"),
        reply(text_block('{"cefr_level": "A'), stop_reason="max_tokens"),  # cut off
    ],
    ids=["not-json", "not-an-object", "no-content", "refusal", "truncated"],
)
async def test_unusable_answers_are_reported_as_invalid(response: SimpleNamespace) -> None:
    with pytest.raises(LLMInvalidResponseError):
        await call(make_client(StubMessages(response)))


@pytest.mark.parametrize(
    "error",
    [
        anthropic.APIConnectionError(request=REQUEST),
        anthropic.APITimeoutError(request=REQUEST),
        anthropic.RateLimitError(
            "slow down", response=httpx2.Response(429, request=REQUEST), body=None
        ),
        anthropic.InternalServerError(
            "boom", response=httpx2.Response(500, request=REQUEST), body=None
        ),
        anthropic.AuthenticationError(
            "bad key", response=httpx2.Response(401, request=REQUEST), body=None
        ),
        anthropic.NotFoundError(
            "no such model", response=httpx2.Response(404, request=REQUEST), body=None
        ),
    ],
    ids=["connection", "timeout", "429", "500", "401", "404"],
)
async def test_provider_failures_become_llm_unavailable(error: Exception) -> None:
    with pytest.raises(LLMUnavailableError):
        await call(make_client(StubMessages(error=error)))


async def test_the_learner_text_is_never_logged(caplog: pytest.LogCaptureFixture) -> None:
    error = anthropic.InternalServerError(
        "boom", response=httpx2.Response(500, request=REQUEST), body=None
    )

    with pytest.raises(LLMUnavailableError):
        await call(make_client(StubMessages(error=error)), "my very private diary entry")

    assert "private diary" not in caplog.text
    assert caplog.records  # the failure WAS logged: the assertion above is not vacuous


# --- generate_exercises: only what differs from analyze_text --------------------------------
# The retry/parsing logic (`_call`) is shared and already covered above; here we only check the
# request shape and that the shared machinery is actually reused for this second entry point.

EXERCISE_ANSWER: dict[str, Any] = {"exercises": []}


async def generate_exercises(
    client: AnthropicClient, failures: list[RuleFailure] | None = None, count: int = 4
) -> dict[str, Any]:
    return await client.generate_exercises(
        rule_failures=failures or [RuleFailure(RuleTag.VERB_TENSE, (("go", "went"),))],
        ui_language=UiLanguage.ES,
        count=count,
    )


async def test_returns_the_parsed_exercise_json() -> None:
    client = make_client(StubMessages(reply(text_block(json.dumps(EXERCISE_ANSWER)))))

    assert await generate_exercises(client) == EXERCISE_ANSWER


async def test_the_exercise_request_carries_the_schema_count_and_rules() -> None:
    messages = StubMessages(reply(text_block(json.dumps(EXERCISE_ANSWER))))

    await generate_exercises(make_client(messages), count=4)

    request = messages.calls[0]
    assert request["output_config"] == {
        "format": {"type": "json_schema", "schema": EXERCISE_JSON_SCHEMA}
    }
    assert "exactly 4" in request["system"]
    assert "Spanish" in request["system"]
    assert request["messages"] == [
        {"role": "user", "content": '<rules>\n- verb_tense: "go" -> "went"\n</rules>'}
    ]


async def test_an_unusable_exercise_answer_is_reported_as_invalid() -> None:
    with pytest.raises(LLMInvalidResponseError):
        await generate_exercises(make_client(StubMessages(reply(text_block("not json")))))


async def test_provider_failure_becomes_llm_unavailable_for_exercises_too() -> None:
    error = anthropic.InternalServerError(
        "boom", response=httpx2.Response(500, request=REQUEST), body=None
    )

    with pytest.raises(LLMUnavailableError):
        await generate_exercises(make_client(StubMessages(error=error)))
