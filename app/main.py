from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1 import auth, demo, diagnostics, exercises, health, me, stats, texts
from app.core.config import get_settings
from app.core.errors import register_exception_handlers
from app.core.rate_limit import limiter


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="Marginalia API", version="0.1.0")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["Authorization", "Content-Type"],
    )
    app.state.limiter = limiter  # slowapi looks the limiter up here
    register_exception_handlers(app)

    api_v1 = APIRouter(prefix="/api/v1")
    api_v1.include_router(health.router)
    api_v1.include_router(auth.router)
    api_v1.include_router(me.router)
    api_v1.include_router(texts.router)
    api_v1.include_router(exercises.router)
    api_v1.include_router(stats.router)
    api_v1.include_router(demo.router)
    api_v1.include_router(diagnostics.router)
    app.include_router(api_v1)
    return app


app = create_app()
