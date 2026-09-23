import json

from httpx import AsyncClient
from sqlalchemy import text as sql
from sqlalchemy.ext.asyncio import AsyncEngine

from app.services.usage import today
from tests.helpers import analyze, generate_exercises, register

EXPORT = "/api/v1/me/export"


async def test_export_requires_authentication(client: AsyncClient) -> None:
    response = await client.get(EXPORT)

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


async def test_an_account_without_texts_exports_its_profile_and_empty_lists(
    client: AsyncClient,
) -> None:
    headers = await register(client)

    body = (await client.get(EXPORT, headers=headers)).json()

    assert body["profile"]["email"] == "ana@example.com"
    assert body["texts"] == []
    assert body["exercises"] == []
    assert body["exercise_attempts"] == []
    assert body["usage"] == []
    assert body["exported_at"]


async def test_export_contains_the_profile_every_text_with_corrections_and_the_usage(
    client: AsyncClient,
) -> None:
    headers = await register(client)
    first = await analyze(client, headers, title="first")
    second = await analyze(client, headers, title="second", text="I has teh book and a apple.")

    body = (await client.get(EXPORT, headers=headers)).json()

    assert body["profile"]["display_name"] == "Ana"
    # Exactly what the detail endpoint returns for each text, corrections included: the export
    # is not a lossy summary. Newest first, like the history.
    assert body["texts"] == [second, first]
    assert [len(t["corrections"]) for t in body["texts"]] == [3, 1]
    assert body["usage"] == [
        {"day": today().isoformat(), "analyses_count": 2, "generations_count": 0}
    ]


async def test_export_contains_every_exercise_with_its_answer_and_every_attempt(
    client: AsyncClient,
) -> None:
    headers = await register(client)
    await analyze(client, headers, text="I has teh book and a apple.")
    exercises = await generate_exercises(client, headers)
    assert "correct_answer" not in exercises[0]  # not revealed before an attempt
    await client.post(
        f"/api/v1/exercises/{exercises[0]['id']}/attempt",
        headers=headers,
        json={"user_answer": "x"},
    )

    body = (await client.get(EXPORT, headers=headers)).json()

    assert len(body["exercises"]) == len(exercises)
    exported = {item["id"]: item for item in body["exercises"]}
    # Unlike GET /exercises, the export DOES reveal the answer: it is the user's own data dump.
    assert all("correct_answer" in item and "explanation" in item for item in exported.values())
    assert exported[exercises[0]["id"]]["status"] == "done"
    [attempt] = body["exercise_attempts"]
    assert attempt["exercise_id"] == exercises[0]["id"]
    assert attempt["attempted_at"]
    assert body["usage"][0]["generations_count"] == 1


async def test_export_never_includes_secrets(client: AsyncClient) -> None:
    headers = await register(client)
    await analyze(client, headers)

    raw = (await client.get(EXPORT, headers=headers)).text

    assert "password" not in raw.lower()
    assert "argon2" not in raw
    assert "correct-horse-battery" not in raw
    assert headers["Authorization"].split()[1] not in raw


async def test_export_only_contains_the_callers_own_data(client: AsyncClient) -> None:
    ana = await register(client, "ana@example.com")
    ben = await register(client, "ben@example.com")
    ana_text = await analyze(client, ana, title="ana's text")
    await analyze(client, ben, title="ben's text")
    ana_exercises = await generate_exercises(client, ana)

    ana_export = (await client.get(EXPORT, headers=ana)).json()
    ben_export = (await client.get(EXPORT, headers=ben)).json()

    assert [t["id"] for t in ana_export["texts"]] == [ana_text["id"]]
    assert ana_export["profile"]["email"] == "ana@example.com"
    assert "ben" not in json.dumps(ana_export).lower()
    assert ben_export["profile"]["email"] == "ben@example.com"
    assert "ana's text" not in json.dumps(ben_export)
    assert {item["id"] for item in ana_export["exercises"]} == {i["id"] for i in ana_exercises}
    assert ben_export["exercises"] == []


async def test_export_is_offered_as_a_file_download(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    response = await client.get(EXPORT, headers=auth_headers)

    assert response.headers["content-type"].startswith("application/json")
    disposition = response.headers["content-disposition"]
    assert disposition.startswith("attachment;")
    assert f"marginalia-export-{today().isoformat()}.json" in disposition


async def test_export_then_delete_leaves_nothing_behind(
    client: AsyncClient, engine: AsyncEngine
) -> None:
    """The two "your data" rights together: take a copy, then erase everything."""
    headers = await register(client)
    await analyze(client, headers)
    assert len((await client.get(EXPORT, headers=headers)).json()["texts"]) == 1

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


async def test_export_is_rate_limited(client: AsyncClient, auth_headers: dict[str, str]) -> None:
    statuses = [(await client.get(EXPORT, headers=auth_headers)).status_code for _ in range(7)]

    # It reads everything the user ever wrote: cheap to ask for, expensive to serve.
    assert statuses[:5] == [200] * 5
    assert statuses[5:] == [429, 429]
