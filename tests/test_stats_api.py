import pytest
from httpx import AsyncClient

from tests.helpers import TEXT, analyze, register

OVERVIEW = "/api/v1/stats/overview"
PROGRESS = "/api/v1/stats/progress"
BY_CATEGORY = "/api/v1/stats/errors-by-category"
TOP_RULES = "/api/v1/stats/top-rules"


@pytest.mark.parametrize("url", [OVERVIEW, PROGRESS, BY_CATEGORY, TOP_RULES])
async def test_every_endpoint_requires_authentication(client: AsyncClient, url: str) -> None:
    response = await client.get(url)

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


async def test_overview_for_a_new_account_is_all_zero(client: AsyncClient) -> None:
    headers = await register(client)

    body = (await client.get(OVERVIEW, headers=headers)).json()

    assert body == {
        "texts_count": 0,
        "words_count": 0,
        "errors_per_100_words": 0.0,
        "current_level": None,
        "streak_days": 0,
    }


async def test_overview_reflects_a_real_analysis(client: AsyncClient) -> None:
    headers = await register(client)
    saved = await analyze(client, headers)  # TEXT has one real (fake-client) mistake

    body = (await client.get(OVERVIEW, headers=headers)).json()

    assert body["texts_count"] == 1
    assert body["words_count"] == saved["word_count"]
    assert body["current_level"] == saved["cefr_level"]
    assert body["streak_days"] == 1
    assert body["errors_per_100_words"] > 0


async def test_progress_lists_a_point_for_a_saved_text(client: AsyncClient) -> None:
    headers = await register(client)
    saved = await analyze(client, headers)

    body = (await client.get(PROGRESS, headers=headers)).json()

    assert len(body) == 1
    assert body[0]["word_count"] == saved["word_count"]
    assert body[0]["day"] == saved["created_at"][:10]


async def test_progress_accepts_the_days_query_param(client: AsyncClient) -> None:
    headers = await register(client)
    await analyze(client, headers)

    response = await client.get(f"{PROGRESS}?days=30", headers=headers)

    assert response.status_code == 200
    assert len(response.json()) == 1


@pytest.mark.parametrize("query", ["days=0", "days=366", "days=abc"])
async def test_an_invalid_days_value_is_rejected(client: AsyncClient, query: str) -> None:
    headers = await register(client)

    for url in (PROGRESS, BY_CATEGORY, TOP_RULES):
        response = await client.get(f"{url}?{query}", headers=headers)
        assert response.status_code == 422, url
        assert response.json()["error"]["code"] == "validation_error"


async def test_errors_by_category_lists_all_five_categories(client: AsyncClient) -> None:
    headers = await register(client)
    await analyze(client, headers)

    body = (await client.get(BY_CATEGORY, headers=headers)).json()

    assert {row["category"] for row in body} == {
        "grammar",
        "spelling",
        "vocabulary",
        "punctuation",
        "style",
    }
    assert sum(row["count"] for row in body) > 0


async def test_top_rules_reflects_the_real_mistake(client: AsyncClient) -> None:
    headers = await register(client)
    await analyze(client, headers)  # a verb_tense mistake

    body = (await client.get(TOP_RULES, headers=headers)).json()

    assert body[0]["rule_tag"] == "verb_tense"
    assert body[0]["count"] == 1


async def test_a_user_never_sees_another_users_statistics(client: AsyncClient) -> None:
    ana = await register(client, "ana@example.com")
    ben = await register(client, "ben@example.com")
    await analyze(client, ana)

    ben_overview = (await client.get(OVERVIEW, headers=ben)).json()
    ben_progress = (await client.get(PROGRESS, headers=ben)).json()
    ben_categories = (await client.get(BY_CATEGORY, headers=ben)).json()
    ben_rules = (await client.get(TOP_RULES, headers=ben)).json()

    assert ben_overview["texts_count"] == 0
    assert ben_progress == []
    assert all(row["count"] == 0 for row in ben_categories)
    assert ben_rules == []


async def test_deleting_a_text_removes_it_from_the_statistics(client: AsyncClient) -> None:
    headers = await register(client)
    saved = await analyze(client, headers)

    assert (await client.delete(f"/api/v1/texts/{saved['id']}", headers=headers)).status_code == 204

    body = (await client.get(OVERVIEW, headers=headers)).json()
    assert body["texts_count"] == 0
    assert (await client.get(TOP_RULES, headers=headers)).json() == []


async def test_the_word_count_default_text_has_nine_words(client: AsyncClient) -> None:
    # Sanity-checks the fixture this whole file leans on: if TEXT ever changes, this fails loudly
    # instead of every assertion above quietly drifting.
    assert len(TEXT.split()) == 9
