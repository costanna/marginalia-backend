from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.core.security import decode_access_token
from app.db.models import User
from tests.conftest import REGISTER_PAYLOAD

REGISTER_URL = "/api/v1/auth/register"
LOGIN_URL = "/api/v1/auth/login"


async def test_register_returns_a_valid_token(client: AsyncClient) -> None:
    response = await client.post(REGISTER_URL, json=REGISTER_PAYLOAD)

    assert response.status_code == 201
    body = response.json()
    assert body["token_type"] == "bearer"
    decode_access_token(body["access_token"])  # raises if it is not a valid token


async def test_register_stores_a_hash_and_a_lowercase_email(
    client: AsyncClient, engine: AsyncEngine
) -> None:
    payload = {**REGISTER_PAYLOAD, "email": "  Ana@Example.COM "}
    assert (await client.post(REGISTER_URL, json=payload)).status_code == 201

    async with async_sessionmaker(engine)() as session:
        user = await session.scalar(select(User))
    assert user is not None
    assert user.email == "ana@example.com"
    assert user.password_hash != REGISTER_PAYLOAD["password"]
    assert user.password_hash.startswith("$argon2id$")


async def test_register_duplicate_email_is_rejected_case_insensitively(
    client: AsyncClient,
) -> None:
    await client.post(REGISTER_URL, json=REGISTER_PAYLOAD)
    response = await client.post(
        REGISTER_URL, json={**REGISTER_PAYLOAD, "email": "ANA@example.com"}
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "email_taken"


async def test_register_rejects_a_short_password(client: AsyncClient) -> None:
    response = await client.post(REGISTER_URL, json={**REGISTER_PAYLOAD, "password": "short"})

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    assert error["details"]["errors"][0]["field"] == "password"


async def test_register_rejects_an_invalid_email_and_unknown_language(client: AsyncClient) -> None:
    bad_email = await client.post(REGISTER_URL, json={**REGISTER_PAYLOAD, "email": "not-an-email"})
    bad_lang = await client.post(REGISTER_URL, json={**REGISTER_PAYLOAD, "ui_language": "de"})

    assert bad_email.status_code == 422
    assert bad_lang.status_code == 422


async def test_register_defaults_the_language_to_spanish(
    client: AsyncClient,
) -> None:
    payload = {k: v for k, v in REGISTER_PAYLOAD.items() if k != "ui_language"}
    token = (await client.post(REGISTER_URL, json=payload)).json()["access_token"]

    me = await client.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"})
    assert me.json()["ui_language"] == "es"


async def test_register_accepts_french(client: AsyncClient) -> None:
    payload = {**REGISTER_PAYLOAD, "ui_language": "fr"}
    response = await client.post(REGISTER_URL, json=payload)
    token = response.json()["access_token"]

    me = await client.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 201
    assert me.json()["ui_language"] == "fr"


async def test_login_with_correct_credentials(client: AsyncClient) -> None:
    await client.post(REGISTER_URL, json=REGISTER_PAYLOAD)

    response = await client.post(
        LOGIN_URL,
        json={"email": "ANA@example.com", "password": REGISTER_PAYLOAD["password"]},
    )

    assert response.status_code == 200
    decode_access_token(response.json()["access_token"])


async def test_login_does_not_reveal_whether_the_email_exists(client: AsyncClient) -> None:
    await client.post(REGISTER_URL, json=REGISTER_PAYLOAD)

    wrong_password = await client.post(
        LOGIN_URL, json={"email": REGISTER_PAYLOAD["email"], "password": "wrong-password"}
    )
    unknown_email = await client.post(
        LOGIN_URL, json={"email": "nobody@example.com", "password": "wrong-password"}
    )

    assert wrong_password.status_code == unknown_email.status_code == 401
    assert wrong_password.json() == unknown_email.json()
    assert wrong_password.json()["error"]["code"] == "invalid_credentials"
