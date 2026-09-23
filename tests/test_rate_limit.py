from collections.abc import Callable
from typing import Any

import pytest
from fastapi import Request
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text as sql
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import get_settings
from app.core.rate_limit import client_ip
from app.main import app
from tests.conftest import REGISTER_PAYLOAD
from tests.helpers import TEXT

DEMO = "/api/v1/demo/analyze"


def make_request(
    forwarded: str | None = None, peer: tuple[str, int] | None = ("10.0.0.1", 5)
) -> Request:
    headers = [(b"x-forwarded-for", forwarded.encode())] if forwarded is not None else []
    return Request({"type": "http", "headers": headers, "client": peer})


@pytest.fixture
def trust_proxies(monkeypatch: pytest.MonkeyPatch) -> Callable[[int], None]:
    def set_hops(hops: int) -> None:
        monkeypatch.setattr(get_settings(), "trusted_proxy_hops", hops)

    return set_hops


@pytest.fixture
def client_from() -> Callable[[str], AsyncClient]:
    """Build clients that appear to connect from a given IP (use with the `client` fixture)."""

    def build(ip: str) -> AsyncClient:
        return AsyncClient(
            transport=ASGITransport(app=app, client=(ip, 4321)), base_url="http://test"
        )

    return build


# --- client_ip ----------------------------------------------------------------------------------


def test_without_trusted_proxies_the_forwarded_header_is_ignored() -> None:
    assert client_ip(make_request(forwarded="6.6.6.6")) == "10.0.0.1"


@pytest.mark.parametrize(
    ("hops", "header", "expected"),
    [
        (1, "203.0.113.7", "203.0.113.7"),
        (1, "1.1.1.1, 203.0.113.7", "203.0.113.7"),  # the client-supplied prefix is not trusted
        (2, "spoofed, 203.0.113.7, 10.9.9.9", "203.0.113.7"),
        (2, "203.0.113.7", "10.0.0.1"),  # fewer entries than proxies: not from our chain
        (1, None, "10.0.0.1"),  # no header at all
        (1, " , ", "10.0.0.1"),  # header with nothing usable
    ],
)
def test_with_trusted_proxies_the_visitor_is_counted_from_the_end(
    trust_proxies: Callable[[int], None], hops: int, header: str | None, expected: str
) -> None:
    trust_proxies(hops)

    assert client_ip(make_request(forwarded=header)) == expected


def test_a_missing_peer_does_not_crash() -> None:
    assert client_ip(make_request(peer=None)) == "unknown"


# --- demo endpoint ------------------------------------------------------------------------------


async def test_the_demo_works_without_an_account(client: AsyncClient) -> None:
    response = await client.post(DEMO, json={"text": TEXT, "ui_language": "en"})

    assert response.status_code == 200
    assert response.json() == {
        "original_text": TEXT,
        "corrected_text": "Yesterday I went to the cinema with my friends.",
        "cefr_level": "A1",
        "word_count": 9,
        "summary": "Good start! I found 1 things to improve; you are close.",
        "ui_language": "en",
        "corrections": [
            {
                "start": 12,
                "end": 14,
                "original": "go",
                "suggestion": "went",
                "category": "grammar",
                "rule_tag": "verb_tense",
                "explanation": "With 'yesterday' use the past simple: 'went'.",
            }
        ],
    }


async def test_the_demo_defaults_to_spanish(client: AsyncClient) -> None:
    body = (await client.post(DEMO, json={"text": TEXT})).json()

    assert body["ui_language"] == "es"
    assert "pasado simple" in body["corrections"][0]["explanation"]


async def test_the_demo_saves_nothing(client: AsyncClient, engine: AsyncEngine) -> None:
    await client.post(DEMO, json={"text": TEXT})

    async with engine.connect() as conn:
        for table in ("users", "texts", "corrections", "usage_counters"):
            assert (await conn.execute(sql(f"SELECT count(*) FROM {table}"))).scalar_one() == 0


@pytest.mark.parametrize(
    ("body", "code"),
    [
        ({"text": "too short"}, "text_too_short"),
        ({"text": "x" * 3001}, "text_too_long"),
        ({}, "validation_error"),
        ({"text": TEXT, "ui_language": "de"}, "validation_error"),
    ],
)
async def test_the_demo_validates_its_input(
    client: AsyncClient, body: dict[str, Any], code: str
) -> None:
    response = await client.post(DEMO, json=body)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == code


async def test_the_demo_is_limited_per_day_per_ip(
    client: AsyncClient, client_from: Callable[[str], AsyncClient]
) -> None:
    limit = get_settings().demo_daily_limit
    for _ in range(limit):
        assert (await client.post(DEMO, json={"text": TEXT})).status_code == 200

    blocked = await client.post(DEMO, json={"text": TEXT})
    async with client_from("198.51.100.9") as other_visitor:
        allowed = await other_visitor.post(DEMO, json={"text": TEXT})

    assert blocked.status_code == 429
    assert blocked.json() == {
        "error": {
            "code": "daily_quota_exceeded",
            "message": "Too many requests.",
            "details": {"limit": limit, "period": "day"},
        }
    }
    assert allowed.status_code == 200


async def test_forging_the_forwarded_header_does_not_evade_the_limit(client: AsyncClient) -> None:
    limit = get_settings().demo_daily_limit
    for i in range(limit):
        await client.post(DEMO, json={"text": TEXT}, headers={"X-Forwarded-For": f"9.9.9.{i}"})

    response = await client.post(DEMO, json={"text": TEXT}, headers={"X-Forwarded-For": "8.8.8.8"})

    assert response.status_code == 429  # no trusted proxies configured: the header is ignored


async def test_behind_a_trusted_proxy_each_visitor_gets_their_own_allowance(
    client: AsyncClient, trust_proxies: Callable[[int], None]
) -> None:
    trust_proxies(1)
    limit = get_settings().demo_daily_limit

    def as_visitor(ip: str) -> dict[str, str]:
        return {"X-Forwarded-For": f"forged, {ip}"}  # the proxy appends the real address last

    for _ in range(limit):
        await client.post(DEMO, json={"text": TEXT}, headers=as_visitor("203.0.113.1"))

    same = await client.post(DEMO, json={"text": TEXT}, headers=as_visitor("203.0.113.1"))
    different = await client.post(DEMO, json={"text": TEXT}, headers=as_visitor("203.0.113.2"))

    assert same.status_code == 429
    assert different.status_code == 200


# --- login and register -------------------------------------------------------------------------


AUTH_REQUESTS: dict[str, tuple[str, dict[str, str]]] = {
    "login": ("/api/v1/auth/login", {"email": "nobody@example.com", "password": "wrong-password"}),
    "register": ("/api/v1/auth/register", REGISTER_PAYLOAD),
}


@pytest.mark.parametrize("endpoint", ["login", "register"])
async def test_auth_endpoints_are_rate_limited_per_minute(
    client: AsyncClient, endpoint: str
) -> None:
    url, body = AUTH_REQUESTS[endpoint]

    statuses = [(await client.post(url, json=body)).status_code for _ in range(21)]
    last = await client.post(url, json=body)

    assert 429 not in statuses[:20]
    assert statuses[20] == 429
    assert last.json()["error"]["code"] == "rate_limit_exceeded"  # per-minute: not a daily quota
    assert last.json()["error"]["details"] == {"limit": 20, "period": "minute"}
