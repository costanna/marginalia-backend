from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict[str, str]:
    """Liveness probe for Render, also used by the frontend to wake the server up.

    It deliberately does not query the database: a slow Neon cold start must not make
    the platform believe the API itself is down.
    """
    return {"status": "ok"}
