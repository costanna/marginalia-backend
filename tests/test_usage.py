import asyncio
import uuid
from collections.abc import AsyncIterator
from datetime import date

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.errors import AppError
from app.db.models import GlobalUsageCounter, UsageCounter, User
from app.services import usage
from app.services.usage import (
    refund_analysis,
    refund_generation,
    reserve_analysis,
    reserve_generation,
    reserve_global_llm_call,
)


@pytest.fixture
async def session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    async with async_sessionmaker(engine, expire_on_commit=False)() as db_session:
        yield db_session


async def make_user(session: AsyncSession, email: str = "ana@example.com") -> uuid.UUID:
    user = User(email=email, password_hash="x", display_name="Ana")
    session.add(user)
    await session.commit()
    return user.id


async def counter(session: AsyncSession, user_id: uuid.UUID) -> int:
    value = await session.scalar(
        select(UsageCounter.analyses_count).where(UsageCounter.user_id == user_id)
    )
    return value or 0


async def test_each_reservation_uses_one_analysis(session: AsyncSession) -> None:
    user_id = await make_user(session)

    await reserve_analysis(session, user_id, limit=3)
    await reserve_analysis(session, user_id, limit=3)

    assert await counter(session, user_id) == 2


async def test_the_limit_is_enforced_and_reported(session: AsyncSession) -> None:
    user_id = await make_user(session)
    for _ in range(3):
        await reserve_analysis(session, user_id, limit=3)

    with pytest.raises(AppError) as error:
        await reserve_analysis(session, user_id, limit=3)

    assert error.value.code == "daily_quota_exceeded"
    assert error.value.status_code == 429
    assert error.value.details == {"limit": 3}
    assert await counter(session, user_id) == 3  # the rejected attempt was not counted


async def test_users_have_separate_allowances(session: AsyncSession) -> None:
    ana = await make_user(session, "ana@example.com")
    ben = await make_user(session, "ben@example.com")
    await reserve_analysis(session, ana, limit=1)

    await reserve_analysis(session, ben, limit=1)  # must not raise

    with pytest.raises(AppError):
        await reserve_analysis(session, ana, limit=1)


async def test_a_new_day_starts_a_new_allowance(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    user_id = await make_user(session)
    monkeypatch.setattr(usage, "today", lambda: date(2026, 9, 21))
    await reserve_analysis(session, user_id, limit=1)

    monkeypatch.setattr(usage, "today", lambda: date(2026, 9, 22))
    day = await reserve_analysis(session, user_id, limit=1)  # must not raise

    assert day == date(2026, 9, 22)


async def test_refund_gives_the_analysis_back(session: AsyncSession) -> None:
    user_id = await make_user(session)
    day = await reserve_analysis(session, user_id, limit=1)

    await refund_analysis(session, user_id, day)

    assert await counter(session, user_id) == 0
    await reserve_analysis(session, user_id, limit=1)  # available again


async def test_refund_never_goes_below_zero(session: AsyncSession) -> None:
    user_id = await make_user(session)
    day = await reserve_analysis(session, user_id, limit=5)

    await refund_analysis(session, user_id, day)
    await refund_analysis(session, user_id, day)

    assert await counter(session, user_id) == 0


async def test_concurrent_requests_cannot_exceed_the_limit(engine: AsyncEngine) -> None:
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as setup:
        user_id = await make_user(setup)

    async def attempt() -> bool:
        async with maker() as own_session:  # each request has its own session, as in the API
            try:
                await reserve_analysis(own_session, user_id, limit=3)
            except AppError:
                return False
            return True

    outcomes = await asyncio.gather(*(attempt() for _ in range(10)))

    assert sum(outcomes) == 3
    async with maker() as check:
        assert await counter(check, user_id) == 3


# --- Exercise generations: the same atomic reservation, a different column -------------------


async def generation_counter(session: AsyncSession, user_id: uuid.UUID) -> int:
    value = await session.scalar(
        select(UsageCounter.generations_count).where(UsageCounter.user_id == user_id)
    )
    return value or 0


async def test_generations_and_analyses_have_independent_allowances(session: AsyncSession) -> None:
    user_id = await make_user(session)

    await reserve_generation(session, user_id, limit=2)
    await reserve_analysis(session, user_id, limit=5)

    assert await generation_counter(session, user_id) == 1
    assert await counter(session, user_id) == 1  # analyses_count untouched by the generation


async def test_the_generation_limit_is_enforced_and_reported(session: AsyncSession) -> None:
    user_id = await make_user(session)
    await reserve_generation(session, user_id, limit=1)

    with pytest.raises(AppError) as error:
        await reserve_generation(session, user_id, limit=1)

    assert error.value.code == "daily_quota_exceeded"
    assert error.value.details == {"limit": 1}
    assert await generation_counter(session, user_id) == 1  # the rejected attempt was not counted


async def test_a_generation_refund_gives_it_back(session: AsyncSession) -> None:
    user_id = await make_user(session)
    day = await reserve_generation(session, user_id, limit=1)

    await refund_generation(session, user_id, day)

    assert await generation_counter(session, user_id) == 0
    await reserve_generation(session, user_id, limit=1)  # available again


# --- reserve_global_llm_call: a shared, per-day cap across every user and the demo ------------


async def global_calls(session: AsyncSession, day: date) -> int:
    value = await session.scalar(
        select(GlobalUsageCounter.llm_calls).where(GlobalUsageCounter.day == day)
    )
    return value or 0


async def test_each_reservation_uses_one_shared_call(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(usage, "today", lambda: date(2099, 6, 1))

    await reserve_global_llm_call(session, limit=3)
    await reserve_global_llm_call(session, limit=3)

    assert await global_calls(session, date(2099, 6, 1)) == 2


async def test_the_limit_has_no_per_user_dimension_at_all(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unlike reserve_analysis/reserve_generation, this call takes no user_id: the cap is one
    shared counter for the whole app, so two different users' calls exhaust the same allowance."""
    monkeypatch.setattr(usage, "today", lambda: date(2099, 6, 2))
    await make_user(session, "ana@example.com")
    await make_user(session, "ben@example.com")

    await reserve_global_llm_call(session, limit=2)  # "ana"'s request, conceptually
    await reserve_global_llm_call(session, limit=2)  # "ben"'s

    with pytest.raises(AppError) as error:
        await reserve_global_llm_call(session, limit=2)  # a third request, either user's

    assert error.value.code == "llm_capacity_reached"
    assert error.value.status_code == 503
    assert error.value.details == {"limit": 2}
    assert await global_calls(session, date(2099, 6, 2)) == 2  # the rejected attempt uncounted


async def test_a_new_day_starts_a_new_shared_allowance(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(usage, "today", lambda: date(2099, 6, 3))
    await reserve_global_llm_call(session, limit=1)

    monkeypatch.setattr(usage, "today", lambda: date(2099, 6, 4))
    await reserve_global_llm_call(session, limit=1)  # must not raise

    assert await global_calls(session, date(2099, 6, 3)) == 1
    assert await global_calls(session, date(2099, 6, 4)) == 1


async def test_concurrent_requests_cannot_exceed_the_shared_limit(
    engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(usage, "today", lambda: date(2099, 6, 5))
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def attempt() -> bool:
        async with maker() as own_session:  # each request has its own session, as in the API
            try:
                await reserve_global_llm_call(own_session, limit=3)
            except AppError:
                return False
            return True

    outcomes = await asyncio.gather(*(attempt() for _ in range(10)))

    assert sum(outcomes) == 3
    async with maker() as check:
        assert await global_calls(check, date(2099, 6, 5)) == 3
