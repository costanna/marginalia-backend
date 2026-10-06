"""The one error format, including the cases nobody wrote a handler for.

These tests are pure: they build their own client, so they run with no PostgreSQL around.
"""

import logging
from collections.abc import Iterator

import pytest
from fastapi import APIRouter, Request
from httpx import ASGITransport, AsyncClient, Response

from app.core.errors import _handle_http_exception, _handle_rate_limit
from app.main import app

PATH = "/api/v1/probe/boom"


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/",
            "query_string": b"",
            "headers": [],
            "server": ("test", 80),
        }
    )


async def test_a_misregistered_http_handler_falls_back_to_the_500_format() -> None:
    """A wrong exception mapping must not 500 inside the error handler itself."""

    response = await _handle_http_exception(_request(), ValueError("boom"))

    assert response.status_code == 500


async def test_a_limitless_rate_limit_falls_back_to_the_500_format() -> None:
    response = await _handle_rate_limit(_request(), ValueError("boom"))

    assert response.status_code == 500


@pytest.fixture
def exploding_route() -> Iterator[str]:
    """Mount a route that blows up, and put the router back the way it was afterwards."""
    original = list(app.router.routes)
    router = APIRouter(prefix="/probe")

    @router.get("/boom")
    async def boom() -> None:
        raise ValueError("kaboom")

    app.include_router(router, prefix="/api/v1")
    yield PATH
    app.router.routes[:] = original


async def _explode(path: str) -> Response:
    # Starlette re-raises once it has answered, so that servers and test clients can see the
    # error; this flag says "answer anyway" instead of letting that raise reach the test.
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get(path)


async def test_an_uncaught_error_still_answers_in_the_documented_format(
    exploding_route: str,
) -> None:
    response = await _explode(exploding_route)

    assert response.status_code == 500
    assert response.headers["content-type"].startswith("application/json")
    error = response.json()["error"]
    assert set(error) == {"code", "message", "details"}
    assert error["code"] == "internal_error"
    assert error["details"] == {}
    # The exception's own text can carry a connection string or a path: it must never go out.
    assert "kaboom" not in response.text


async def test_the_route_and_the_exception_type_are_logged(
    exploding_route: str, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.ERROR, logger="app.core.errors"):
        await _explode(exploding_route)

    # The server log already carries the traceback, but not which path exploded.
    assert "ValueError" in caplog.text
    assert PATH in caplog.text


async def test_a_known_error_keeps_its_own_code() -> None:
    """The 500 handler sits behind the specific ones, it does not replace them."""
    response = await _explode("/api/v1/definitely-not-a-route")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
