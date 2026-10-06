"""Shared test setup: a real PostgreSQL test database, migrated with Alembic.

Tests run against `<DATABASE_URL database>_test`, created on demand, so development data is
never touched. In CI, DATABASE_URL points to the PostgreSQL service container.

Two kinds of test live side by side:

* the PURE ones (validators, prompts, statistics maths, config, clients) need nothing but the
  interpreter, and are what you run in the edit-run-fix loop;
* the DATABASE ones ask for `engine` / `client` / `session`, which pull in `migrated_database`
  and therefore a running PostgreSQL. Without one they are skipped with the command to fix it.
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
# Pin everything a developer's .env could change, so tests are reproducible and can NEVER reach
# the real LLM (or spend money) even if a real key is configured locally.
os.environ["LLM_PROVIDER"] = "fake"
os.environ["LLM_BASE_URL"] = ""
os.environ["LLM_API_KEY"] = ""
os.environ["LLM_MODEL"] = ""
os.environ["MAX_TEXT_CHARS"] = "3000"
os.environ["DAILY_ANALYSIS_LIMIT"] = "10"
os.environ["DAILY_GENERATION_LIMIT"] = "5"
os.environ["DEMO_DAILY_LIMIT"] = "2"

from app.core.config import get_settings  # noqa: E402

_dev_url = make_url(get_settings().database_url)
_test_db_name = f"{_dev_url.database}_test"
TEST_DATABASE_URL = _dev_url.set(database=_test_db_name).render_as_string(hide_password=False)
os.environ["DATABASE_URL"] = TEST_DATABASE_URL
get_settings.cache_clear()

from app.core.rate_limit import limiter  # noqa: E402
from app.db.session import get_session  # noqa: E402
from app.main import app  # noqa: E402
from app.services.llm import get_llm_client  # noqa: E402
from app.services.llm.fake_client import FakeLLMClient  # noqa: E402

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


# Reason PostgreSQL was found to be down, or None when it has not been probed (or answered).
# Cached so a machine without a database pays the connection timeout once, not once per test.
_database_down: str | None = None


def _probe_database() -> str | None:
    """Return None when PostgreSQL answers, otherwise a message explaining the skip."""
    global _database_down
    if _database_down is not None:
        return _database_down

    admin_url = _dev_url.set(drivername="postgresql", database="postgres")
    try:
        with psycopg.connect(
            admin_url.render_as_string(hide_password=False),
            connect_timeout=3,
            autocommit=True,
        ) as conn:
            conn.execute("SELECT 1")
    except psycopg.OperationalError as exc:
        # psycopg's message spans several lines (one per attempted address); the first is enough.
        detail = str(exc).splitlines()[0]
        _database_down = (
            f"PostgreSQL is not reachable at {_dev_url.host}:{_dev_url.port} "
            f"(start it with `docker compose up -d db`): {detail}"
        )
        return _database_down
    return None


@pytest.fixture(scope="session")
def migrated_database() -> Iterator[None]:
    """Build the schema from scratch with the real migrations (this also tests them).

    Session-scoped but deliberately NOT autouse: it is requested by `engine`, so only the tests
    that actually talk to the database run it. The pure ones (validators, prompts, statistics
    maths, config) then run on any machine, with no server at all — which is what makes this
    suite usable inside a tight edit-run-fix loop instead of only in CI.

    When PostgreSQL is down, the database-backed tests are SKIPPED with the command that fixes
    it. In CI a missing database is a broken job, not a reason to go green, so there it fails.
    """
    reason = _probe_database()
    if reason is not None:
        if os.environ.get("CI"):
            pytest.fail(reason, pytrace=False)
        pytest.skip(reason)

    _create_test_database_if_missing()
    config = Config(str(ALEMBIC_INI))
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    yield


@pytest.fixture(autouse=True)
def reset_rate_limits() -> None:
    """Counters live in memory and would otherwise leak from one test into the next."""
    limiter.reset()


@pytest.fixture
async def engine(migrated_database: Iterator[None]) -> AsyncIterator[AsyncEngine]:
    # `migrated_database` is what ties the whole database-backed group to a real schema; asking
    # for `engine` (or anything built on it) is enough to opt in.
    # NullPool: every test gets fresh connections bound to its own event loop.
    test_engine = create_async_engine(
        TEST_DATABASE_URL, poolclass=NullPool, connect_args={"prepare_threshold": None}
    )
    yield test_engine
    async with test_engine.begin() as conn:
        # global_usage_counters has no user FK, so CASCADE from users would never reach it: without
        # this, the shared daily LLM quota accumulates across the whole session and a new test can
        # push it over the limit for no visible reason.
        await conn.execute(text("TRUNCATE users, global_usage_counters CASCADE"))
    await test_engine.dispose()


@pytest.fixture
async def client(engine: AsyncEngine) -> AsyncIterator[AsyncClient]:
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def override_session() -> AsyncIterator[AsyncSession]:
        async with maker() as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_llm_client] = FakeLLMClient  # tests may override it again
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        yield http
    app.dependency_overrides.clear()


@pytest.fixture
async def auth_headers(client: AsyncClient) -> dict[str, str]:
    """Register a user and return the Authorization header for it."""
    response = await client.post("/api/v1/auth/register", json=REGISTER_PAYLOAD)
    assert response.status_code == 201
    return {"Authorization": f"Bearer {response.json()['access_token']}"}
