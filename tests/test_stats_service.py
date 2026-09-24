from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.db.models import (
    AnalyzedText,
    Category,
    CefrLevel,
    Correction,
    RuleTag,
    UiLanguage,
    User,
)
from app.services.stats import errors_by_category, overview, progress, top_rules

# Anchored to the real clock, not a fixed date: `overview()` compares against
# `datetime.now(UTC)` internally (streak_days especially needs "today" to really be today), so a
# hardcoded NOW would start failing streak_days assertions the day after it was written.
NOW = datetime.now(UTC).replace(hour=12, minute=0, second=0, microsecond=0)


@pytest.fixture
async def session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    async with async_sessionmaker(engine, expire_on_commit=False)() as db_session:
        yield db_session


@pytest.fixture
async def user(session: AsyncSession) -> User:
    new_user = User(email="ana@example.com", password_hash="x", display_name="Ana")
    session.add(new_user)
    await session.commit()
    return new_user


async def make_text(
    session: AsyncSession,
    user: User,
    *,
    word_count: int = 10,
    when: datetime = NOW,
    level: CefrLevel = CefrLevel.B1,
    corrections: tuple[tuple[Category, RuleTag], ...] = (),
) -> AnalyzedText:
    text = AnalyzedText(
        user_id=user.id,
        original_text="x",
        corrected_text="y",
        cefr_level=level,
        word_count=word_count,
        summary="x",
        ui_language_used=UiLanguage.EN,
        model_name="fake",
        created_at=when,
        corrections=[
            Correction(
                start_offset=0,
                end_offset=1,
                original_fragment="x",
                suggestion="y",
                category=category,
                rule_tag=rule_tag,
                explanation="x",
                position=i,
            )
            for i, (category, rule_tag) in enumerate(corrections)
        ],
    )
    session.add(text)
    await session.commit()
    return text


# --- overview ----------------------------------------------------------------------------------


async def test_overview_for_a_new_user_is_all_zero(session: AsyncSession, user: User) -> None:
    result = await overview(session, user.id)

    assert result == {
        "texts_count": 0,
        "words_count": 0,
        "errors_per_100_words": 0.0,
        "current_level": None,
        "streak_days": 0,
    }


async def test_overview_totals_words_texts_and_errors(session: AsyncSession, user: User) -> None:
    await make_text(
        session, user, word_count=50, corrections=((Category.GRAMMAR, RuleTag.VERB_TENSE),) * 3
    )
    await make_text(session, user, word_count=50, corrections=((Category.SPELLING, RuleTag.OTHER),))

    result = await overview(session, user.id)

    assert result["texts_count"] == 2
    assert result["words_count"] == 100
    assert result["errors_per_100_words"] == 4.0  # 4 errors / 100 words * 100


async def test_current_level_is_the_most_recent_texts_level(
    session: AsyncSession, user: User
) -> None:
    await make_text(session, user, level=CefrLevel.A2, when=NOW - timedelta(days=1))
    await make_text(session, user, level=CefrLevel.B2, when=NOW)

    result = await overview(session, user.id)

    assert result["current_level"] == CefrLevel.B2


async def test_streak_reflects_real_consecutive_days_in_the_database(
    session: AsyncSession, user: User
) -> None:
    for i in range(3):
        await make_text(session, user, when=NOW - timedelta(days=i))

    result = await overview(session, user.id)

    assert result["streak_days"] == 3


async def test_two_texts_the_same_day_count_as_one_streak_day(
    session: AsyncSession, user: User
) -> None:
    await make_text(session, user, when=NOW)
    await make_text(session, user, when=NOW + timedelta(hours=2))

    result = await overview(session, user.id)

    assert result["streak_days"] == 1


async def test_overview_never_counts_another_users_texts(session: AsyncSession, user: User) -> None:
    other = User(email="ben@example.com", password_hash="x", display_name="Ben")
    session.add(other)
    await session.commit()
    await make_text(
        session, other, word_count=999, corrections=((Category.GRAMMAR, RuleTag.OTHER),)
    )

    result = await overview(session, user.id)

    assert result["texts_count"] == 0 and result["words_count"] == 0


# --- progress ------------------------------------------------------------------------------------


async def test_progress_is_empty_without_any_texts(session: AsyncSession, user: User) -> None:
    assert await progress(session, user.id, days=90) == []


async def test_progress_has_one_point_per_active_day_oldest_first(
    session: AsyncSession, user: User
) -> None:
    await make_text(session, user, word_count=20, when=NOW - timedelta(days=2))
    await make_text(
        session,
        user,
        word_count=50,
        when=NOW,
        corrections=((Category.GRAMMAR, RuleTag.VERB_TENSE),) * 2,
    )

    points = await progress(session, user.id, days=90)

    assert [p["word_count"] for p in points] == [20, 50]  # oldest first
    assert points[0]["errors_per_100_words"] == 0.0
    assert points[1]["errors_per_100_words"] == 4.0  # 2 / 50 * 100


async def test_progress_merges_same_day_texts_into_one_point(
    session: AsyncSession, user: User
) -> None:
    await make_text(
        session, user, word_count=10, when=NOW, corrections=((Category.GRAMMAR, RuleTag.OTHER),)
    )
    await make_text(session, user, word_count=10, when=NOW + timedelta(hours=1))

    points = await progress(session, user.id, days=90)

    assert len(points) == 1
    assert points[0]["word_count"] == 20
    assert points[0]["errors_per_100_words"] == 5.0  # 1 error / 20 words * 100


async def test_progress_excludes_texts_outside_the_window(
    session: AsyncSession, user: User
) -> None:
    await make_text(session, user, when=NOW - timedelta(days=40))

    assert await progress(session, user.id, days=30) == []
    assert len(await progress(session, user.id, days=90)) == 1


async def test_progress_never_mixes_in_another_users_texts(
    session: AsyncSession, user: User
) -> None:
    other = User(email="ben@example.com", password_hash="x", display_name="Ben")
    session.add(other)
    await session.commit()
    await make_text(session, other)

    assert await progress(session, user.id, days=90) == []


# --- errors_by_category ---------------------------------------------------------------------------


async def test_errors_by_category_lists_every_category_zero_filled(
    session: AsyncSession, user: User
) -> None:
    result = await errors_by_category(session, user.id, days=30)

    assert {row["category"] for row in result} == set(Category)
    assert all(row["count"] == 0 for row in result)


async def test_errors_by_category_counts_correctly(session: AsyncSession, user: User) -> None:
    await make_text(
        session,
        user,
        corrections=(
            (Category.GRAMMAR, RuleTag.VERB_TENSE),
            (Category.GRAMMAR, RuleTag.ARTICLES),
            (Category.SPELLING, RuleTag.SPELLING_COMMON),
        ),
    )

    result = await errors_by_category(session, user.id, days=30)

    counts = {row["category"]: row["count"] for row in result}
    assert counts[Category.GRAMMAR] == 2
    assert counts[Category.SPELLING] == 1
    assert counts[Category.VOCABULARY] == 0


async def test_errors_by_category_excludes_texts_outside_the_window(
    session: AsyncSession, user: User
) -> None:
    await make_text(
        session,
        user,
        when=NOW - timedelta(days=40),
        corrections=((Category.GRAMMAR, RuleTag.OTHER),),
    )

    result = await errors_by_category(session, user.id, days=30)

    assert all(row["count"] == 0 for row in result)


# --- top_rules -------------------------------------------------------------------------------


async def test_top_rules_ranks_by_frequency(session: AsyncSession, user: User) -> None:
    await make_text(
        session,
        user,
        corrections=(
            (Category.GRAMMAR, RuleTag.VERB_TENSE),
            (Category.GRAMMAR, RuleTag.VERB_TENSE),
            (Category.GRAMMAR, RuleTag.ARTICLES),
        ),
    )

    result = await top_rules(session, user.id, days=30)

    assert result[0] == {"rule_tag": RuleTag.VERB_TENSE, "count": 2}
    assert result[1] == {"rule_tag": RuleTag.ARTICLES, "count": 1}


async def test_top_rules_ties_are_broken_by_name(session: AsyncSession, user: User) -> None:
    await make_text(
        session,
        user,
        corrections=(
            (Category.SPELLING, RuleTag.SPELLING_COMMON),
            (Category.GRAMMAR, RuleTag.ARTICLES),
        ),
    )

    result = await top_rules(session, user.id, days=30)

    assert [r["rule_tag"] for r in result] == [RuleTag.ARTICLES, RuleTag.SPELLING_COMMON]


async def test_top_rules_is_limited_to_five(session: AsyncSession, user: User) -> None:
    all_tags = list(RuleTag)[:7]
    await make_text(session, user, corrections=tuple((Category.GRAMMAR, tag) for tag in all_tags))

    result = await top_rules(session, user.id, days=30)

    assert len(result) == 5


async def test_top_rules_is_empty_without_any_mistakes(session: AsyncSession, user: User) -> None:
    assert await top_rules(session, user.id, days=30) == []
