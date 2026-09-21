import pytest
from httpx import AsyncClient

from app.core.config import get_settings

URL = "/api/v1/diagnostics/client-ip"


async def test_it_does_not_exist_unless_it_is_enabled(client: AsyncClient) -> None:
    response = await client.get(URL)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


async def test_it_is_hidden_from_the_public_documentation(client: AsyncClient) -> None:
    schema = (await client.get("/openapi.json")).json()

    assert not any("diagnostics" in path for path in schema["paths"])


async def test_when_enabled_it_shows_how_the_caller_reaches_the_api(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "enable_diagnostics", True)
    monkeypatch.setattr(get_settings(), "trusted_proxy_hops", 2)

    response = await client.get(
        URL,
        headers={
            "X-Forwarded-For": "203.0.113.5, 198.51.100.7, 172.71.0.9",
            "CF-Connecting-IP": "203.0.113.5",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["x_forwarded_for"] == "203.0.113.5, 198.51.100.7, 172.71.0.9"
    assert body["cf_connecting_ip"] == "203.0.113.5"
    assert body["trusted_proxy_hops"] == 2
    assert body["rate_limit_key"] == "198.51.100.7"  # second from the end, as configured
    assert body["peer"]  # the socket peer


async def test_missing_headers_are_reported_as_null(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "enable_diagnostics", True)

    body = (await client.get(URL)).json()

    assert body["x_forwarded_for"] is None
    assert body["cf_connecting_ip"] is None
    assert body["trusted_proxy_hops"] == 0
