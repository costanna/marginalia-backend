from typing import Any

from fastapi import APIRouter, Request

from app.api.v1.deps import SettingsDep
from app.core.errors import AppError
from app.core.rate_limit import client_ip

# Not part of the public API: hidden from the documentation and disabled unless ENABLE_DIAGNOSTICS.
router = APIRouter(prefix="/diagnostics", tags=["diagnostics"], include_in_schema=False)


@router.get("/client-ip")
async def client_ip_diagnostics(request: Request, settings: SettingsDep) -> dict[str, Any]:
    """Shows how the caller's address reaches the API, to choose TRUSTED_PROXY_HOPS correctly.

    Everything returned is what the CALLER itself sent (their own headers and address), so it
    reveals nothing about anyone else. It is off by default: enable it, look, and disable it.
    """
    if not settings.enable_diagnostics:
        raise AppError(code="not_found", message="Not found.", status_code=404)
    headers = request.headers
    return {
        "x_forwarded_for": headers.get("x-forwarded-for"),
        "x_real_ip": headers.get("x-real-ip"),
        "cf_connecting_ip": headers.get("cf-connecting-ip"),
        "forwarded": headers.get("forwarded"),
        "peer": request.client.host if request.client else None,
        "trusted_proxy_hops": settings.trusted_proxy_hops,
        "rate_limit_key": client_ip(request),
    }
