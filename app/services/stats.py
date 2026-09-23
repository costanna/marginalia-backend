"""Progress statistics: KPIs, a streak, and three chart-ready breakdowns.

Everything here reads `texts` and `corrections`; nothing is stored separately, so there is no
migration for this phase and nothing to keep in sync. Offsets into the "day" a text belongs to are
always UTC, matching the daily quota counters (see app/services/usage.py).
"""

import uuid
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import ColumnElement, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from app.db.models import AnalyzedText, Category, Correction

TOP_RULES_LIMIT = 5


def _utc_day(column: InstrumentedAttribute[datetime]) -> ColumnElement[date]:
    """The calendar day (UTC) a timestamptz column falls on, independent of the DB session's
    own timezone setting."""
    return func.date(func.timezone("UTC", column))


def compute_streak(active_days: set[date], today: date) -> int:
    """Consecutive days with at least one analysed text, counting back from today.

    A day with no activity YET does not break a streak that is still alive: if today has nothing
    but yesterday does, the streak is still counted (today is not over). Two idle days in a row
    end it.
    """
    if today in active_days:
        cursor = today
    elif (today - timedelta(days=1)) in active_days:
        cursor = today - timedelta(days=1)
    else:
        return 0
    streak = 0
    while cursor in active_days:
        streak += 1
        cursor -= timedelta(days=1)
    return streak


async def overview(session: AsyncSession, user_id: uuid.UUID) -> dict[str, object]:
    totals = (
        await session.execute(
            select(
                func.count(AnalyzedText.id),
                func.coalesce(func.sum(AnalyzedText.word_count), 0),
            ).where(AnalyzedText.user_id == user_id)
        )
    ).one()
    texts_count, words_count = totals

    errors_count = (
        await session.scalar(
            select(func.count(Correction.id))
            .join(AnalyzedText, AnalyzedText.id == Correction.text_id)
            .where(AnalyzedText.user_id == user_id)
        )
        or 0
    )
    errors_per_100_words = round(errors_count / words_count * 100, 1) if words_count else 0.0

    current_level = await session.scalar(
        select(AnalyzedText.cefr_level)
        .where(AnalyzedText.user_id == user_id)
        .order_by(AnalyzedText.created_at.desc())
        .limit(1)
    )

    active_days = {
        row[0]
        for row in await session.execute(
            select(_utc_day(AnalyzedText.created_at))
            .where(AnalyzedText.user_id == user_id)
            .distinct()
        )
    }
    streak_days = compute_streak(active_days, datetime.now(UTC).date())

    return {
        "texts_count": texts_count,
        "words_count": words_count,
        "errors_per_100_words": errors_per_100_words,
        "current_level": current_level,
        "streak_days": streak_days,
    }


async def progress(
    session: AsyncSession, user_id: uuid.UUID, *, days: int
) -> list[dict[str, object]]:
    """One point per day that has at least one text, oldest first. Days with none are omitted:
    a sparse time series is exactly what a line chart with a time scale expects."""
    since = datetime.now(UTC) - timedelta(days=days)
    day_column = _utc_day(AnalyzedText.created_at)
    # Aggregate per TEXT first: joining corrections directly into a per-day SUM(word_count) would
    # count a text's words once for every one of its corrections (a classic join fan-out) — a text
    # with 2 corrections would count its words twice, one with none would count them once, and the
    # per-day total would be wrong either way.
    per_text = (
        select(
            AnalyzedText.id,
            day_column.label("day"),
            AnalyzedText.word_count.label("words"),
            func.count(Correction.id).label("errors"),
        )
        .outerjoin(Correction, Correction.text_id == AnalyzedText.id)
        .where(AnalyzedText.user_id == user_id, AnalyzedText.created_at >= since)
        .group_by(AnalyzedText.id)
        .subquery()
    )
    rows = await session.execute(
        select(
            per_text.c.day,
            func.sum(per_text.c.words).label("words"),
            func.sum(per_text.c.errors).label("errors"),
        )
        .group_by(per_text.c.day)
        .order_by(per_text.c.day)
    )
    return [
        {
            "day": row.day,
            "errors_per_100_words": round(row.errors / row.words * 100, 1) if row.words else 0.0,
            "word_count": row.words,
        }
        for row in rows
    ]


async def errors_by_category(
    session: AsyncSession, user_id: uuid.UUID, *, days: int
) -> list[dict[str, object]]:
    since = datetime.now(UTC) - timedelta(days=days)
    rows = await session.execute(
        select(Correction.category, func.count(Correction.id))
        .join(AnalyzedText, AnalyzedText.id == Correction.text_id)
        .where(AnalyzedText.user_id == user_id, AnalyzedText.created_at >= since)
        .group_by(Correction.category)
    )
    counts = {category: count for category, count in rows}
    # Every category, even at zero: a donut chart needs every slice to keep its own colour and
    # legend entry stable, whether or not the user has ever made that kind of mistake.
    return [{"category": category, "count": counts.get(category, 0)} for category in Category]


async def top_rules(
    session: AsyncSession, user_id: uuid.UUID, *, days: int
) -> list[dict[str, object]]:
    since = datetime.now(UTC) - timedelta(days=days)
    rows = await session.execute(
        select(Correction.rule_tag, func.count(Correction.id).label("n"))
        .join(AnalyzedText, AnalyzedText.id == Correction.text_id)
        .where(AnalyzedText.user_id == user_id, AnalyzedText.created_at >= since)
        .group_by(Correction.rule_tag)
        .order_by(func.count(Correction.id).desc(), Correction.rule_tag)
        .limit(TOP_RULES_LIMIT)
    )
    return [{"rule_tag": row.rule_tag, "count": row.n} for row in rows]
