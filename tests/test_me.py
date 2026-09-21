import uuid
from datetime import timedelta

from httpx import AsyncClient

from app.core.security import create_access_token
from tests.conftest import REGISTER_PAYLOAD

ME_URL = "/api/v1/me"


async def test_me_requires_authentication(client: AsyncClient) -> None:
    response = await client.get(ME_URL)

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"
    assert response.headers["www-authenticate"] == "Bearer"


async def test_me_rejects_garbage_expired_and_unknown_user_tokens(client: AsyncClient) -> None:
    expired = create_access_token(uuid.uuid4(), timedelta(seconds=-1))
    unknown_user = create_access_token(uuid.uuid4())

    for token in ("not-a-jwt", expired, unknown_user):
        response = await client.get(ME_URL, headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 401, token
        assert response.json()["error"]["code"] == "unauthorized"


async def test_get_me_returns_the_profile_without_sensitive_fields(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    response = await client.get(ME_URL, headers=auth_headers)

    assert response.status_code == 200
    body = response.json()
    assert body["email"] == REGISTER_PAYLOAD["email"]
    assert body["display_name"] == "Ana"
    assert body["ui_language"] == "ca"
    assert body["theme_preference"] == "system"
    assert body["target_level"] is None
    assert "password" not in body and "password_hash" not in body


async def test_patch_me_updates_only_the_fields_sent(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    response = await client.patch(
        ME_URL, headers=auth_headers, json={"theme_preference": "dark", "target_level": "B2"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["theme_preference"] == "dark"
    assert body["target_level"] == "B2"
    assert body["display_name"] == "Ana"  # untouched
    assert (await client.get(ME_URL, headers=auth_headers)).json()["theme_preference"] == "dark"


async def test_patch_me_can_clear_the_target_level_but_not_required_fields(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    await client.patch(ME_URL, headers=auth_headers, json={"target_level": "C1"})

    response = await client.patch(
        ME_URL, headers=auth_headers, json={"target_level": None, "display_name": None}
    )

    body = response.json()
    assert body["target_level"] is None
    assert body["display_name"] == "Ana"


async def test_patch_me_validates_input(client: AsyncClient, auth_headers: dict[str, str]) -> None:
    for bad_body in (
        {"target_level": "A1"},  # A1 is not a valid target
        {"theme_preference": "blue"},
        {"ui_language": "fr"},
        {"display_name": "   "},
        {"email": "other@example.com"},  # email is not editable
        {"password": "another-password"},  # password is not editable here
    ):
        response = await client.patch(ME_URL, headers=auth_headers, json=bad_body)
        assert response.status_code == 422, bad_body
        assert response.json()["error"]["code"] == "validation_error"


async def test_delete_me_removes_the_account_and_invalidates_the_token(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    response = await client.delete(ME_URL, headers=auth_headers)
    assert response.status_code == 204

    # The JWT itself is still cryptographically valid, but the account is gone.
    assert (await client.get(ME_URL, headers=auth_headers)).status_code == 401
    login = await client.post(
        "/api/v1/auth/login",
        json={"email": REGISTER_PAYLOAD["email"], "password": REGISTER_PAYLOAD["password"]},
    )
    assert login.status_code == 401


async def test_users_only_see_their_own_profile(client: AsyncClient) -> None:
    tokens = []
    for email in ("a@example.com", "b@example.com"):
        r = await client.post("/api/v1/auth/register", json={**REGISTER_PAYLOAD, "email": email})
        tokens.append(r.json()["access_token"])

    emails = [
        (await client.get(ME_URL, headers={"Authorization": f"Bearer {t}"})).json()["email"]
        for t in tokens
    ]

    assert emails == ["a@example.com", "b@example.com"]
