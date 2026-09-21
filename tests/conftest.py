"""Shared test setup: a real PostgreSQL test database, migrated with Alembic.

Tests run against `<DATABASE_URL database>_test`, created on demand, so development data is
never touched. In CI, DATABASE_URL points to the PostgreSQL service container.
"""

import asyncio
import os
import sys
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from httpx import ASGITransport, AsyncClient
from psycopg import sql
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

# psycopg's async mode cannot run on Windows' default ProactorEventLoop.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# The environment must be configured BEFORE any `app` module reads the settings, which is
# why the app imports below carry `noqa: E402`.
os.environ.setdefault("SECRET_KEY", "test-secret-key-that-is-long-enough-for-hs256-signing")
os.environ["ENVIRONMENT"] = "test"

from app.core.config import get_settings  # noqa: E402

_dev_url = make_url(get_settings().database_url)
_test_db_name = f"{_dev_url.database}_test"
TEST_DATABASE_URL = _dev_url.set(database=_test_db_name).render_as_string(hide_password=False)
os.environ["DATABASE_URL"] = TEST_DATABASE_URL
get_settings.cache_clear()

from app.db.session import get_session  # noqa: E402
from app.main import app  # noqa: E402

ALEMBIC_INI = Path(__file__).resolve().parent.parent / "alembic.ini"

REGISTER_PAYLOAD = {
    "email": "ana@example.com",
    "password": "correct-horse-battery",
    "display_name": "Ana",
    "ui_language": "ca",
}


def _create_test_database_if_missing() -> None:
    admin_url = _dev_url.set(drivername="postgresql", database="postgres")
    with psycopg.connect(admin_url.render_as_string(hide_password=False), autocommit=True) as conn:
        exists = conn.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", (_test_db_name,)
        ).fetchone()
        if not exists:
            conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(_test_db_name)))


@pytest.fixture(scope="session", autouse=True)
def migrated_database() -> Iterator[None]:
    """Build the schema from scratch with the real migrations (this also tests them)."""
    _create_test_database_if_missing()
    config = Config(str(ALEMBIC_INI))
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    yield


@pytest.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    # NullPool: every test gets fresh connections bound to its own event loop.
    test_engine = create_async_engine(
        TEST_DATABASE_URL, poolclass=NullPool, connect_args={"prepare_threshold": None}
    )
    yield test_engine
    async with test_engine.begin() as conn:
        await conn.execute(text("TRUNCATE users CASCADE"))
    await test_engine.dispose()


@pytest.fixture
async def client(engine: AsyncEngine) -> AsyncIterator[AsyncClient]:
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def override_session() -> AsyncIterator[AsyncSession]:
        async with maker() as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        yield http
    app.dependency_overrides.clear()


@pytest.fixture
async def auth_headers(client: AsyncClient) -> dict[str, str]:
    """Register a user and return the Authorization header for it."""
    response = await client.post("/api/v1/auth/register", json=REGISTER_PAYLOAD)
    assert response.status_code == 201
    return {"Authorization": f"Bearer {response.json()['access_token']}"}
