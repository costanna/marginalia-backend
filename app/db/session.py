from collections.abc import AsyncIterator
from functools import lru_cache

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings


@lru_cache
def get_engine() -> AsyncEngine:
    return create_async_engine(
        get_settings().database_url,
        # Neon suspends idle compute and closes connections: check them before use
        # and recycle them before the server does.
        pool_pre_ping=True,
        pool_recycle=300,
        # Neon's pooled endpoint sits behind PgBouncer, which does not support prepared statements.
        connect_args={"prepare_threshold": None},
    )


@lru_cache
def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    # expire_on_commit=False: objects stay readable after commit without an implicit reload
    # (which would be a lazy load, and lazy loads are not allowed in async code).
    return async_sessionmaker(get_engine(), expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: one session per request."""
    async with get_sessionmaker()() as session:
        yield session
