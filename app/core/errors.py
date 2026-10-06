"""Single error format: {"error": {"code": ..., "message": ..., "details": {...}}}.

The backend never translates messages: the frontend maps `code` to a translated text.
"""

import logging
from collections.abc import Mapping
from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from slowapi.errors import RateLimitExceeded
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)


class AppError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        status_code: int,
        details: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or {}
        self.headers = headers


def _error_response(
    status_code: int,
    code: str,
    message: str,
    details: dict[str, Any] | None = None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    body = {"error": {"code": code, "message": message, "details": details or {}}}
    return JSONResponse(status_code=status_code, content=jsonable_encoder(body), headers=headers)


# Status codes raised by the framework itself (routing, method checks) mapped to stable codes.
_HTTP_STATUS_CODES = {
    401: "unauthorized",
    404: "not_found",
    429: "rate_limit_exceeded",
}


async def _handle_app_error(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, AppError)
    return _error_response(exc.status_code, exc.code, exc.message, exc.details, exc.headers)


async def _handle_validation_error(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    # `ctx` may hold non-serializable objects (e.g. the original exception); keep it out.
    errors = [
        {"field": ".".join(str(p) for p in e["loc"][1:]), "type": e["type"], "message": e["msg"]}
        for e in exc.errors()
    ]
    return _error_response(422, "validation_error", "Invalid request data.", {"errors": errors})


async def _handle_http_exception(request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, StarletteHTTPException):
        # A wrong registration must not 500 inside the error handler: fall back to the
        # generic 500 path (an assert would also vanish under `python -O`).
        return await _handle_unexpected(request, exc)
    code = _HTTP_STATUS_CODES.get(exc.status_code, "http_error")
    return _error_response(exc.status_code, code, str(exc.detail), headers=exc.headers)


async def _handle_rate_limit(request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, RateLimitExceeded) or exc.limit is None:
        # Same reasoning as above: never 500 inside an error handler.
        return await _handle_unexpected(request, exc)
    item = exc.limit.limit
    period = item.GRANULARITY.name
    # A per-day limit is a quota the user can act on ("sign up for more"); anything shorter
    # is plain abuse protection ("slow down").
    code = "daily_quota_exceeded" if period == "day" else "rate_limit_exceeded"
    return _error_response(
        429, code, "Too many requests.", {"limit": item.amount, "period": period}
    )


async def _handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
    """Keep an exception nobody caught inside the format this module documents.

    Without a handler, Starlette's own default answers `text/plain "Internal Server Error"`, so a
    client written against `{"error": {...}}` would find no `code` to show. The traceback is not
    duplicated here: Starlette re-raises once the response is sent (see
    `starlette/middleware/errors.py`), so the server logs it with its own context. What the server
    log does NOT say is which path exploded, so only the route and the exception type are added —
    never `str(exc)`, which could carry a connection string or a file path to the client.
    """
    logger.error("Unhandled %s on %s %s", type(exc).__name__, request.method, request.url.path)
    return _error_response(500, "internal_error", "An unexpected error occurred.")


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, _handle_app_error)
    # RateLimitExceeded is an HTTPException subclass: its own handler must be registered too.
    app.add_exception_handler(RateLimitExceeded, _handle_rate_limit)
    app.add_exception_handler(RequestValidationError, _handle_validation_error)
    app.add_exception_handler(StarletteHTTPException, _handle_http_exception)
    # Keyed by STATUS CODE: Starlette routes 500 to ServerErrorMiddleware, which runs before the
    # others and so is the only one that can catch an exception raised outside a route (a
    # dependency, middleware, ...) or one that nobody handled. It must be registered before the
    # first request, because that is when the middleware stack is built from this dict.
    app.add_exception_handler(500, _handle_unexpected)
