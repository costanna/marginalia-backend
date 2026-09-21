"""Single error format: {"error": {"code": ..., "message": ..., "details": {...}}}.

The backend never translates messages: the frontend maps `code` to a translated text.
"""

from collections.abc import Mapping
from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from slowapi.errors import RateLimitExceeded
from starlette.exceptions import HTTPException as StarletteHTTPException


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


async def _handle_http_exception(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    code = _HTTP_STATUS_CODES.get(exc.status_code, "http_error")
    return _error_response(exc.status_code, code, str(exc.detail), headers=exc.headers)


async def _handle_rate_limit(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RateLimitExceeded) and exc.limit is not None
    item = exc.limit.limit
    period = item.GRANULARITY.name
    # A per-day limit is a quota the user can act on ("sign up for more"); anything shorter
    # is plain abuse protection ("slow down").
    code = "daily_quota_exceeded" if period == "day" else "rate_limit_exceeded"
    return _error_response(
        429, code, "Too many requests.", {"limit": item.amount, "period": period}
    )


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, _handle_app_error)
    # RateLimitExceeded is an HTTPException subclass: its own handler must be registered too.
    app.add_exception_handler(RateLimitExceeded, _handle_rate_limit)
    app.add_exception_handler(RequestValidationError, _handle_validation_error)
    app.add_exception_handler(StarletteHTTPException, _handle_http_exception)
