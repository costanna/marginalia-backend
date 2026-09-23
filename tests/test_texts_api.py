import uuid
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import text as sql
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import get_settings
from app.main import app
from app.services.llm import get_llm_client
from app.services.llm.base import LLMInvalidResponseError, LLMUnavailableError
from tests.helpers import TEXT, analyze, generate_exercises, register

ANALYZE = "/api/v1/texts/analyze"
TEXTS = "/api/v1/texts"


class FailingClient:
    model_name = "failing"

    def __init__(self, error: Exception) -> None:
        self.error = error

    async def analyze_text(self, **_: object) -> dict[str, Any]:
        raise self.error


# --- POST /texts/analyze ------------------------------------------------------------------------


async def test_analyze_requires_authentication(client: AsyncClient) -> None:
    response = await client.post(ANALYZE, json={"text": TEXT})

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


async def test_analyze_returns_the_documented_response(client: AsyncClient) -> None:
    headers = await register(client)

    response = await client.post(
        ANALYZE, headers=headers, json={"title": "My weekend", "ui_language": "es", "text": TEXT}
    )

    assert response.status_code == 201
    body = response.json()
    uuid.UUID(body["id"])
    assert body["title"] == "My weekend"
    assert body["original_text"] == TEXT
    assert body["corrected_text"] == "Yesterday I went to the cinema with my friends."
    assert body["word_count"] == 9
    assert body["cefr_level"] in {"A1", "A2", "B1", "B2", "C1", "C2"}
    assert body["ui_language"] == "es"
    assert body["created_at"].endswith("Z") or "+" in body["created_at"]
    [correction] = body["corrections"]
    uuid.UUID(correction.pop("id"))
    assert correction == {
        "start": 12,
        "end": 14,
        "original": "go",
        "suggestion": "went",
        "category": "grammar",
        "rule_tag": "verb_tense",
        "explanation": "Con 'yesterday' se usa el pasado simple: 'went'.",
    }


async def test_the_explanation_language_defaults_to_the_profile_and_can_be_overridden(
    client: AsyncClient,
) -> None:
    headers = await register(client)  # the test user's profile language is Catalan

    default = await analyze(client, headers)
    overridden = await analyze(client, headers, ui_language="en")

    assert "passat simple" in default["corrections"][0]["explanation"]
    assert overridden["ui_language"] == "en"
    assert "past simple" in overridden["corrections"][0]["explanation"]


async def test_a_blank_title_is_stored_as_no_title(client: AsyncClient) -> None:
    headers = await register(client)

    assert (await analyze(client, headers, title="   "))["title"] is None
    assert (await analyze(client, headers))["title"] is None


@pytest.mark.parametrize(
    ("body", "status_code", "code"),
    [
        ({"text": "too short"}, 422, "text_too_short"),
        ({"text": "x" * 3001}, 422, "text_too_long"),
        ({"text": "x" * 20_001}, 422, "validation_error"),
        ({}, 422, "validation_error"),
        ({"text": TEXT, "unknown": 1}, 422, "validation_error"),
        ({"text": TEXT, "ui_language": "de"}, 422, "validation_error"),
        ({"text": TEXT, "title": "t" * 201}, 422, "validation_error"),
    ],
)
async def test_analyze_rejects_invalid_requests(
    client: AsyncClient, body: dict[str, Any], status_code: int, code: str
) -> None:
    headers = await register(client)

    response = await client.post(ANALYZE, headers=headers, json=body)

    assert response.status_code == status_code
    assert response.json()["error"]["code"] == code


async def test_text_length_error_reports_the_limits(client: AsyncClient) -> None:
    headers = await register(client)

    response = await client.post(ANALYZE, headers=headers, json={"text": "short"})

    assert response.json()["error"]["details"] == {"min": 20, "max": 3000, "length": 5}


async def test_the_daily_limit_is_enforced_per_user(client: AsyncClient) -> None:
    limit = get_settings().daily_analysis_limit
    ana = await register(client, "ana@example.com")
    ben = await register(client, "ben@example.com")
    for _ in range(limit):
        await analyze(client, ana)

    blocked = await client.post(ANALYZE, headers=ana, json={"text": TEXT})
    other_user = await client.post(ANALYZE, headers=ben, json={"text": TEXT})

    assert blocked.status_code == 429
    assert blocked.json()["error"]["code"] == "daily_quota_exceeded"
    assert blocked.json()["error"]["details"] == {"limit": limit}
    assert other_user.status_code == 201


@pytest.mark.parametrize(
    ("error", "status_code", "code"),
    [
        (LLMUnavailableError(), 503, "llm_unavailable"),
        (LLMInvalidResponseError("bad"), 502, "llm_invalid_response"),
    ],
)
async def test_llm_failures_are_reported_and_do_not_use_up_the_allowance(
    client: AsyncClient, error: Exception, status_code: int, code: str
) -> None:
    headers = await register(client)
    app.dependency_overrides[get_llm_client] = lambda: FailingClient(error)
    limit = get_settings().daily_analysis_limit

    for _ in range(limit + 1):  # more failures than the daily limit would allow
        response = await client.post(ANALYZE, headers=headers, json={"text": TEXT})
        assert response.status_code == status_code
        assert response.json()["error"]["code"] == code

    app.dependency_overrides.pop(get_llm_client)
    await analyze(client, headers)  # the allowance is intact


async def test_offsets_are_code_points_even_with_emoji(client: AsyncClient) -> None:
    headers = await register(client)

    body = await analyze(client, headers, text="😀😀 Yesterday I go to the cinema, I has time.")

    original = body["original_text"]
    assert [original[c["start"] : c["end"]] for c in body["corrections"]] == ["go", "I has"]


# --- GET /texts (history) -----------------------------------------------------------------------


async def test_history_requires_authentication(client: AsyncClient) -> None:
    assert (await client.get(TEXTS)).status_code == 401


async def test_history_is_empty_for_a_new_user(client: AsyncClient) -> None:
    headers = await register(client)

    response = await client.get(TEXTS, headers=headers)

    assert response.json() == {"items": [], "page": 1, "page_size": 12, "total": 0}


async def test_history_lists_newest_first_with_the_correction_count(client: AsyncClient) -> None:
    headers = await register(client)
    first = await analyze(client, headers, title="first")
    second = await analyze(client, headers, title="second", text="I has teh book and a apple.")

    body = (await client.get(TEXTS, headers=headers)).json()

    assert [item["id"] for item in body["items"]] == [second["id"], first["id"]]
    assert [item["corrections_count"] for item in body["items"]] == [3, 1]
    assert body["total"] == 2
    assert set(body["items"][0]) == {
        "id",
        "title",
        "cefr_level",
        "word_count",
        "corrections_count",
        "created_at",
    }  # the list carries no text bodies


async def test_history_pagination(client: AsyncClient) -> None:
    headers = await register(client)
    ids = [(await analyze(client, headers, title=str(i)))["id"] for i in range(5)]
    newest_first = list(reversed(ids))

    pages = [
        (await client.get(f"{TEXTS}?page={page}&page_size=2", headers=headers)).json()
        for page in (1, 2, 3, 4)
    ]

    assert [[item["id"] for item in p["items"]] for p in pages] == [
        newest_first[0:2],
        newest_first[2:4],
        newest_first[4:5],
        [],  # past the end: empty, not an error
    ]
    assert all(p["total"] == 5 and p["page_size"] == 2 for p in pages)


A2_TEXT = " ".join(
    ["Yesterday I go to the cinema with my friends and we eat pizza"] * 2
)  # 24 words


async def test_history_can_be_filtered_by_level(client: AsyncClient) -> None:
    headers = await register(client)
    a1 = await analyze(client, headers, title="short")  # 9 words -> A1
    a2 = await analyze(client, headers, title="longer", text=A2_TEXT)  # 24 words -> A2
    assert (a1["cefr_level"], a2["cefr_level"]) == ("A1", "A2")

    only_a2 = (await client.get(f"{TEXTS}?level=A2", headers=headers)).json()
    only_a1 = (await client.get(f"{TEXTS}?level=A1", headers=headers)).json()
    nothing = (await client.get(f"{TEXTS}?level=C2", headers=headers)).json()

    assert [i["id"] for i in only_a2["items"]] == [a2["id"]]
    assert only_a2["total"] == 1  # the total follows the filter, so the pager stays correct
    assert [i["id"] for i in only_a1["items"]] == [a1["id"]]
    assert nothing["items"] == [] and nothing["total"] == 0
    assert (await client.get(TEXTS, headers=headers)).json()["total"] == 2  # no filter: all


async def test_the_level_filter_never_reaches_other_users_texts(client: AsyncClient) -> None:
    ana = await register(client, "ana@example.com")
    ben = await register(client, "ben@example.com")
    await analyze(client, ana)

    listing = (await client.get(f"{TEXTS}?level=A1", headers=ben)).json()

    assert listing["items"] == [] and listing["total"] == 0


async def test_the_level_filter_rejects_unknown_levels(client: AsyncClient) -> None:
    headers = await register(client)

    for query in ("level=Z9", "level=b1", "level="):
        response = await client.get(f"{TEXTS}?{query}", headers=headers)
        assert response.status_code == 422, query
        assert response.json()["error"]["code"] == "validation_error"


@pytest.mark.parametrize("query", ["page=0", "page=-1", "page_size=0", "page_size=51", "page=x"])
async def test_history_rejects_bad_pagination(client: AsyncClient, query: str) -> None:
    headers = await register(client)

    response = await client.get(f"{TEXTS}?{query}", headers=headers)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


# --- GET /texts/{id} ----------------------------------------------------------------------------


async def test_detail_returns_the_full_analysis(client: AsyncClient) -> None:
    headers = await register(client)
    created = await analyze(client, headers)

    response = await client.get(f"{TEXTS}/{created['id']}", headers=headers)

    assert response.status_code == 200
    assert response.json() == created


async def test_detail_of_a_missing_text_is_404(client: AsyncClient) -> None:
    headers = await register(client)

    response = await client.get(f"{TEXTS}/{uuid.uuid4()}", headers=headers)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


async def test_detail_with_a_malformed_id_is_a_validation_error(client: AsyncClient) -> None:
    headers = await register(client)

    response = await client.get(f"{TEXTS}/not-a-uuid", headers=headers)

    assert response.status_code == 422


# --- Isolation between users --------------------------------------------------------------------


async def test_a_user_cannot_read_list_or_delete_another_users_texts(client: AsyncClient) -> None:
    ana = await register(client, "ana@example.com")
    ben = await register(client, "ben@example.com")
    ana_text = await analyze(client, ana)

    read = await client.get(f"{TEXTS}/{ana_text['id']}", headers=ben)
    delete = await client.delete(f"{TEXTS}/{ana_text['id']}", headers=ben)
    listing = (await client.get(TEXTS, headers=ben)).json()

    # Indistinguishable from a text that does not exist: no information leaks.
    assert read.status_code == delete.status_code == 404
    assert (
        read.json()
        == delete.json()
        == {"error": {"code": "not_found", "message": "Text not found.", "details": {}}}
    )
    assert listing["total"] == 0
    assert (await client.get(f"{TEXTS}/{ana_text['id']}", headers=ana)).status_code == 200


# --- DELETE /texts/{id} -------------------------------------------------------------------------


async def test_delete_removes_the_text_and_its_corrections(
    client: AsyncClient, engine: AsyncEngine
) -> None:
    headers = await register(client)
    created = await analyze(client, headers)

    response = await client.delete(f"{TEXTS}/{created['id']}", headers=headers)

    assert response.status_code == 204
    assert (await client.get(f"{TEXTS}/{created['id']}", headers=headers)).status_code == 404
    assert (await client.delete(f"{TEXTS}/{created['id']}", headers=headers)).status_code == 404
    async with engine.connect() as conn:
        for table in ("texts", "corrections"):
            assert (await conn.execute(sql(f"SELECT count(*) FROM {table}"))).scalar_one() == 0


async def test_deleting_the_account_deletes_texts_exercises_and_usage(
    client: AsyncClient, engine: AsyncEngine
) -> None:
    headers = await register(client)
    await analyze(client, headers)
    exercises = await generate_exercises(client, headers)
    await client.post(
        f"/api/v1/exercises/{exercises[0]['id']}/attempt",
        headers=headers,
        json={"user_answer": "x"},
    )

    assert (await client.delete("/api/v1/me", headers=headers)).status_code == 204

    async with engine.connect() as conn:
        for table in (
            "users",
            "texts",
            "corrections",
            "usage_counters",
            "exercises",
            "exercise_attempts",
        ):
            assert (await conn.execute(sql(f"SELECT count(*) FROM {table}"))).scalar_one() == 0
