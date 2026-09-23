"""Daily usage limits, enforced in the database so they hold across requests and processes."""

import uuid
from datetime import UTC, date, datetime

from sqlalchemy import func, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from app.core.errors import AppError
from app.db.models import UsageCounter


def today() -> date:
    """The current day in UTC (a function so tests can move the clock)."""
    return datetime.now(UTC).date()


async def _reserve(
    session: AsyncSession,
    user_id: uuid.UUID,
    limit: int,
    column: InstrumentedAttribute[int],
    error_code: str,
    error_message: str,
) -> date:
    """Atomically take one unit of `column` from today's allowance and commit it.

    A single INSERT .. ON CONFLICT DO UPDATE .. WHERE count < limit both checks and increments:
    the row lock makes concurrent requests queue up, so they can never exceed the limit (a
    "SELECT the count, then increment" would let them all pass the check together).

    Returns the day that was charged, so a refund goes to the same day even across midnight.
    """
    day = today()
    statement = (
        pg_insert(UsageCounter)
        .values(user_id=user_id, day=day, **{column.key: 1})
        .on_conflict_do_update(
            index_elements=[UsageCounter.user_id, UsageCounter.day],
            set_={column.key: column + 1},
            where=column < limit,
        )
        .returning(column)
    )
    reserved = (await session.execute(statement)).scalar_one_or_none()
    if reserved is None:
        await session.rollback()
        raise AppError(
            code=error_code, message=error_message, status_code=429, details={"limit": limit}
        )
    await session.commit()
    return day


async def _refund(
    session: AsyncSession, user_id: uuid.UUID, day: date, column: InstrumentedAttribute[int]
) -> None:
    """Give back a reservation when the request failed: the learner should not pay for it."""
    await session.execute(
        update(UsageCounter)
        .where(UsageCounter.user_id == user_id, UsageCounter.day == day)
        .values(**{column.key: func.greatest(column - 1, 0)})
    )
    await session.commit()


async def reserve_analysis(session: AsyncSession, user_id: uuid.UUID, limit: int) -> date:
    return await _reserve(
        session,
        user_id,
        limit,
        UsageCounter.analyses_count,
        "daily_quota_exceeded",
        "Daily analysis limit reached.",
    )


async def refund_analysis(session: AsyncSession, user_id: uuid.UUID, day: date) -> None:
    await _refund(session, user_id, day, UsageCounter.analyses_count)


async def reserve_generation(session: AsyncSession, user_id: uuid.UUID, limit: int) -> date:
    return await _reserve(
        session,
        user_id,
        limit,
        UsageCounter.generations_count,
        "daily_quota_exceeded",
        "Daily exercise generation limit reached.",
    )


async def refund_generation(session: AsyncSession, user_id: uuid.UUID, day: date) -> None:
    await _refund(session, user_id, day, UsageCounter.generations_count)
