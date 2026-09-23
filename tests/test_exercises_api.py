import uuid
from typing import Any

import pytest
from httpx import AsyncClient

from app.core.config import get_settings
from app.db.models import ExerciseType
from app.main import app
from app.services.llm import get_llm_client
from app.services.llm.base import LLMInvalidResponseError, LLMUnavailableError
from tests.helpers import analyze, generate_exercises, register

GENERATE = "/api/v1/exercises/generate"
EXERCISES = "/api/v1/exercises"


class FailingClient:
    model_name = "failing"

    def __init__(self, error: Exception) -> None:
        self.error = error

    async def analyze_text(self, **_: object) -> dict[str, Any]:
        raise NotImplementedError

    async def generate_exercises(self, **_: object) -> dict[str, Any]:
        raise self.error


def attempt_url(exercise_id: str) -> str:
    return f"{EXERCISES}/{exercise_id}/attempt"


# --- POST /exercises/generate ---------------------------------------------------------------


async def test_generate_requires_authentication(client: AsyncClient) -> None:
    response = await client.post(GENERATE)

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


async def test_generate_is_empty_without_any_recent_mistakes(client: AsyncClient) -> None:
    headers = await register(client)

    response = await client.post(GENERATE, headers=headers)

    assert response.status_code == 200
    assert response.json() == []


async def test_generate_builds_exercises_from_the_users_mistakes(client: AsyncClient) -> None:
    headers = await register(client)
    await analyze(client, headers)  # TEXT has a real (fake-client) verb_tense mistake

    exercises = await generate_exercises(client, headers)

    assert len(exercises) > 0
    for item in exercises:
        assert item["status"] == "pending"
        assert "correct_answer" not in item  # never revealed before an attempt
        assert "explanation" not in item
        if item["type"] == ExerciseType.MULTIPLE_CHOICE.value:
            assert isinstance(item["options"], list) and len(item["options"]) >= 2
        else:
            assert item["options"] is None
            assert item["prompt"].count("___") == 1


async def test_generate_reuses_pending_exercises_on_a_second_call(client: AsyncClient) -> None:
    headers = await register(client)
    await analyze(client, headers)
    first = await generate_exercises(client, headers)

    second = await generate_exercises(client, headers)

    assert [item["id"] for item in second] == [item["id"] for item in first]


async def test_generate_makes_a_fresh_batch_once_the_previous_one_is_done(
    client: AsyncClient,
) -> None:
    headers = await register(client)
    await analyze(client, headers)
    first = await generate_exercises(client, headers)
    for item in first:
        await client.post(attempt_url(item["id"]), headers=headers, json={"user_answer": "x"})

    second = await generate_exercises(client, headers)

    assert {item["id"] for item in second}.isdisjoint({item["id"] for item in first})


async def test_the_daily_generation_limit_is_enforced(client: AsyncClient) -> None:
    limit = get_settings().daily_generation_limit
    headers = await register(client)
    await analyze(client, headers)
    for _ in range(limit):
        batch = await generate_exercises(client, headers)
        for item in batch:
            await client.post(attempt_url(item["id"]), headers=headers, json={"user_answer": "x"})

    blocked = await client.post(GENERATE, headers=headers)

    assert blocked.status_code == 429
    assert blocked.json()["error"]["code"] == "daily_quota_exceeded"
    assert blocked.json()["error"]["details"] == {"limit": limit}


@pytest.mark.parametrize(
    ("error", "status_code", "code"),
    [
        (LLMUnavailableError(), 503, "llm_unavailable"),
        (LLMInvalidResponseError("bad"), 502, "llm_invalid_response"),
    ],
)
async def test_llm_failures_are_reported_and_do_not_spend_the_allowance(
    client: AsyncClient, error: Exception, status_code: int, code: str
) -> None:
    headers = await register(client)
    await analyze(client, headers)
    app.dependency_overrides[get_llm_client] = lambda: FailingClient(error)

    response = await client.post(GENERATE, headers=headers)

    assert response.status_code == status_code
    assert response.json()["error"]["code"] == code

    app.dependency_overrides.pop(get_llm_client)
    assert len(await generate_exercises(client, headers)) > 0  # the allowance is intact


# --- GET /exercises --------------------------------------------------------------------------


async def test_list_requires_authentication(client: AsyncClient) -> None:
    assert (await client.get(EXERCISES)).status_code == 401


async def test_list_is_empty_for_a_new_user(client: AsyncClient) -> None:
    headers = await register(client)

    response = await client.get(EXERCISES, headers=headers)

    assert response.status_code == 200
    assert response.json() == []


async def test_list_matches_what_generate_returned(client: AsyncClient) -> None:
    headers = await register(client)
    await analyze(client, headers)
    generated = await generate_exercises(client, headers)

    listed = (await client.get(EXERCISES, headers=headers)).json()

    assert [item["id"] for item in listed] == [item["id"] for item in generated]


async def test_the_status_filter_separates_pending_from_done(client: AsyncClient) -> None:
    headers = await register(client)
    await analyze(client, headers)
    generated = await generate_exercises(client, headers)
    await client.post(attempt_url(generated[0]["id"]), headers=headers, json={"user_answer": "x"})

    pending = (await client.get(f"{EXERCISES}?status=pending", headers=headers)).json()
    done = (await client.get(f"{EXERCISES}?status=done", headers=headers)).json()

    assert generated[0]["id"] not in [item["id"] for item in pending]
    assert [item["id"] for item in done] == [generated[0]["id"]]
    assert len(pending) + len(done) == len(generated)


async def test_an_unknown_status_value_is_rejected(client: AsyncClient) -> None:
    headers = await register(client)

    response = await client.get(f"{EXERCISES}?status=finished", headers=headers)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


async def test_a_user_never_sees_another_users_exercises(client: AsyncClient) -> None:
    ana = await register(client, "ana@example.com")
    ben = await register(client, "ben@example.com")
    await analyze(client, ana)
    await generate_exercises(client, ana)

    assert (await client.get(EXERCISES, headers=ben)).json() == []


# --- POST /exercises/{id}/attempt ------------------------------------------------------------


async def test_attempt_requires_authentication(client: AsyncClient) -> None:
    response = await client.post(attempt_url(str(uuid.uuid4())), json={"user_answer": "x"})

    assert response.status_code == 401


async def test_a_correct_answer_is_graded_and_reveals_the_explanation(
    client: AsyncClient,
) -> None:
    headers = await register(client)
    await analyze(client, headers)
    [exercise] = (await generate_exercises(client, headers))[:1]
    # Read the real answer straight from the database export, the one place it is ever revealed.
    exported = (await client.get("/api/v1/me/export", headers=headers)).json()
    correct_answer = next(
        e["correct_answer"] for e in exported["exercises"] if e["id"] == exercise["id"]
    )

    response = await client.post(
        attempt_url(exercise["id"]), headers=headers, json={"user_answer": correct_answer}
    )

    assert response.status_code == 201
    body = response.json()
    assert body == {
        "is_correct": True,
        "correct_answer": correct_answer,
        "explanation": body["explanation"],
    }
    assert body["explanation"]


async def test_an_incorrect_answer_is_graded_as_such(client: AsyncClient) -> None:
    headers = await register(client)
    await analyze(client, headers)
    [exercise, *_] = await generate_exercises(client, headers)

    response = await client.post(
        attempt_url(exercise["id"]), headers=headers, json={"user_answer": "definitely-wrong"}
    )

    assert response.status_code == 201
    assert response.json()["is_correct"] is False


async def test_an_exercise_cannot_be_attempted_twice(client: AsyncClient) -> None:
    headers = await register(client)
    await analyze(client, headers)
    [exercise, *_] = await generate_exercises(client, headers)
    await client.post(attempt_url(exercise["id"]), headers=headers, json={"user_answer": "x"})

    second = await client.post(
        attempt_url(exercise["id"]), headers=headers, json={"user_answer": "y"}
    )

    assert second.status_code == 409
    assert second.json()["error"]["code"] == "exercise_already_attempted"


async def test_attempt_rejects_invalid_bodies(client: AsyncClient) -> None:
    headers = await register(client)
    await analyze(client, headers)
    [exercise, *_] = await generate_exercises(client, headers)

    for bad_body in ({}, {"user_answer": ""}, {"user_answer": "x", "extra": 1}):
        response = await client.post(attempt_url(exercise["id"]), headers=headers, json=bad_body)
        assert response.status_code == 422, bad_body
        assert response.json()["error"]["code"] == "validation_error"


async def test_attempting_a_missing_exercise_is_404(client: AsyncClient) -> None:
    headers = await register(client)

    response = await client.post(
        attempt_url(str(uuid.uuid4())), headers=headers, json={"user_answer": "x"}
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


async def test_attempt_rejects_a_malformed_id(client: AsyncClient) -> None:
    headers = await register(client)

    response = await client.post(
        attempt_url("not-a-uuid"), headers=headers, json={"user_answer": "x"}
    )

    assert response.status_code == 422


async def test_a_user_cannot_attempt_someone_elses_exercise(client: AsyncClient) -> None:
    ana = await register(client, "ana@example.com")
    ben = await register(client, "ben@example.com")
    await analyze(client, ana)
    [exercise, *_] = await generate_exercises(client, ana)

    response = await client.post(
        attempt_url(exercise["id"]), headers=ben, json={"user_answer": "x"}
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
