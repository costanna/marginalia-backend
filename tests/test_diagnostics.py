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


@pytest.mark.parametrize(
    "forwarded",
    [
        # What Render sends: visitor, Cloudflare edge, Render's internal balancer (measured).
        "79.153.4.54, 172.68.135.73, 10.30.62.133",
        # The same visitor with a forged entry in front, and a different internal balancer.
        "1.2.3.4,79.153.4.54, 172.70.243.171, 10.28.225.255",
    ],
)
async def test_on_render_three_hops_identify_the_visitor_and_ignore_forged_entries(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch, forwarded: str
) -> None:
    monkeypatch.setattr(get_settings(), "enable_diagnostics", True)
    monkeypatch.setattr(get_settings(), "trusted_proxy_hops", 3)

    body = (await client.get(URL, headers={"X-Forwarded-For": forwarded})).json()

    assert body["rate_limit_key"] == "79.153.4.54"
