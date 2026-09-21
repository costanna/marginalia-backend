"""OpenAICompatibleClient tests. No network: httpx's MockTransport plays the provider."""

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from app.db.models import TargetLevel, UiLanguage
from app.services.llm.base import LLMInvalidResponseError, LLMUnavailableError
from app.services.llm.openai_compatible_client import OpenAICompatibleClient

ANSWER = {"cefr_level": "A2", "summary": "Nice.", "corrections": []}
API_KEY = "test-key-should-never-be-logged"

Handler = Callable[[httpx.Request], httpx.Response]


def completion(content: object, finish_reason: str = "stop") -> dict[str, Any]:
    return {"choices": [{"message": {"content": content}, "finish_reason": finish_reason}]}


def ok(content: object = json.dumps(ANSWER), finish_reason: str = "stop") -> httpx.Response:
    return httpx.Response(200, json=completion(content, finish_reason))


class Provider:
    """Scripted fake provider: answers with the queued responses (or raises queued errors)."""

    def __init__(self, *outcomes: httpx.Response | Exception) -> None:
        self.outcomes = list(outcomes)
        self.requests: list[httpx.Request] = []
        self.sleeps: list[float] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)

    def client(self, **overrides: Any) -> OpenAICompatibleClient:
        return OpenAICompatibleClient(
            base_url="https://llm.example.com/v1/",
            api_key=API_KEY,
            model="some-model",
            timeout_seconds=30,
            max_tokens=999,
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(self.handler)),
            sleep=self.sleep,
            **overrides,
        )


async def call(client: OpenAICompatibleClient, text: str = "Hello world.") -> dict[str, Any]:
    return await client.analyze_text(
        text=text, ui_language=UiLanguage.ES, target_level=TargetLevel.B1
    )


# --- The request --------------------------------------------------------------------------------


async def test_the_request_follows_the_openai_chat_protocol() -> None:
    provider = Provider(ok())

    await call(provider.client(), "My text.")

    [request] = provider.requests
    body = json.loads(request.content)
    assert str(request.url) == "https://llm.example.com/v1/chat/completions"  # slash normalised
    assert request.headers["authorization"] == f"Bearer {API_KEY}"
    assert body["model"] == "some-model"
    assert body["max_tokens"] == 999
    assert body["response_format"] == {"type": "json_object"}
    system, user = body["messages"]
    assert system["role"] == "system" and "Spanish" in system["content"]
    assert "aiming for level B1" in system["content"]
    assert "Reply with ONE JSON object" in system["content"]  # the shape is described in the prompt
    assert user == {"role": "user", "content": "<user_text>\nMy text.\n</user_text>"}


# --- Reading the answer -------------------------------------------------------------------------


async def test_returns_the_parsed_json() -> None:
    assert await call(Provider(ok()).client()) == ANSWER


@pytest.mark.parametrize(
    "content",
    [
        "```json\n" + json.dumps(ANSWER) + "\n```",
        "```\n" + json.dumps(ANSWER) + "\n```",
        "Sure! Here you go:\n" + json.dumps(ANSWER) + "\nHope it helps.",
        "  " + json.dumps(ANSWER) + "  ",
    ],
    ids=["json-fence", "plain-fence", "chatter-around", "whitespace"],
)
async def test_lenient_about_how_models_wrap_the_json(content: str) -> None:
    assert await call(Provider(ok(content)).client()) == ANSWER


@pytest.mark.parametrize(
    "response",
    [
        ok("not json at all"),
        ok("[1, 2]"),  # JSON, but not an object
        ok("{ broken"),
        ok(""),
        ok(None),  # e.g. a refusal
        ok(json.dumps(ANSWER), finish_reason="length"),  # cut off
        httpx.Response(200, json={"choices": []}),
        httpx.Response(200, json={"unexpected": True}),
        httpx.Response(200, text="<html>gateway</html>"),
    ],
    ids=[
        "not-json",
        "not-object",
        "broken",
        "empty",
        "null-content",
        "truncated",
        "no-choices",
        "wrong-shape",
        "not-json-body",
    ],
)
async def test_unusable_answers_are_reported_as_invalid(response: httpx.Response) -> None:
    with pytest.raises(LLMInvalidResponseError):
        await call(Provider(response).client())


# --- Failures and retries -----------------------------------------------------------------------


@pytest.mark.parametrize("status", [429, 500, 502, 503, 408])
async def test_transient_errors_are_retried_with_growing_waits(status: int) -> None:
    provider = Provider(httpx.Response(status), httpx.Response(status), ok())

    assert await call(provider.client()) == ANSWER

    assert len(provider.requests) == 3
    assert provider.sleeps == [1.0, 2.0]


async def test_a_retry_after_header_is_honoured_but_capped() -> None:
    provider = Provider(
        httpx.Response(429, headers={"retry-after": "3"}),
        httpx.Response(429, headers={"retry-after": "9999"}),
        ok(),
    )

    await call(provider.client())

    assert provider.sleeps == [3.0, 10.0]


async def test_a_garbage_retry_after_falls_back_to_the_normal_backoff() -> None:
    provider = Provider(httpx.Response(429, headers={"retry-after": "soon"}), ok())

    await call(provider.client())

    assert provider.sleeps == [1.0]


async def test_gives_up_after_the_last_retry() -> None:
    provider = Provider(*[httpx.Response(429)] * 3, ok())

    with pytest.raises(LLMUnavailableError):
        await call(provider.client(max_retries=2))

    assert len(provider.requests) == 3  # the initial attempt plus two retries, never a fourth


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
async def test_client_errors_are_not_retried(status: int) -> None:
    provider = Provider(httpx.Response(status), ok())

    with pytest.raises(LLMUnavailableError):
        await call(provider.client())

    assert len(provider.requests) == 1
    assert provider.sleeps == []


async def test_connection_errors_and_timeouts_are_retried() -> None:
    request = httpx.Request("POST", "https://llm.example.com/v1/chat/completions")
    provider = Provider(
        httpx.ConnectError("refused", request=request),
        httpx.ReadTimeout("slow", request=request),
        ok(),
    )

    assert await call(provider.client()) == ANSWER
    assert provider.sleeps == [1.0, 2.0]


async def test_persistent_connection_failure_is_llm_unavailable() -> None:
    request = httpx.Request("POST", "https://llm.example.com/v1/chat/completions")
    provider = Provider(*[httpx.ConnectError("refused", request=request)] * 3)

    with pytest.raises(LLMUnavailableError):
        await call(provider.client())


async def test_no_retries_when_configured_with_zero() -> None:
    provider = Provider(httpx.Response(500), ok())

    with pytest.raises(LLMUnavailableError):
        await call(provider.client(max_retries=0))

    assert len(provider.requests) == 1


# --- Privacy ------------------------------------------------------------------------------------


async def test_neither_the_text_nor_the_key_is_ever_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    request = httpx.Request("POST", "https://llm.example.com/v1/chat/completions")
    provider = Provider(
        httpx.Response(500),
        httpx.ConnectError("refused", request=request),
        httpx.Response(401),
    )

    with pytest.raises(LLMUnavailableError):
        await call(provider.client(), "my very private diary entry")

    assert "private diary" not in caplog.text
    assert API_KEY not in caplog.text
    assert caplog.records  # something WAS logged (status codes), just nothing sensitive
