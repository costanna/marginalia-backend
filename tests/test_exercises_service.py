import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.errors import AppError
from app.db.models import (
    AnalyzedText,
    Category,
    CefrLevel,
    Correction,
    Exercise,
    ExerciseStatus,
    RuleTag,
    UiLanguage,
    UsageCounter,
    User,
)
from app.services.exercises import (
    ALREADY_ATTEMPTED,
    attempt,
    generate_or_reuse,
    top_rule_failures,
)
from app.services.llm.base import LLMInvalidResponseError, LLMUnavailableError
from app.services.llm.fake_client import FakeLLMClient

NOW = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)
LIMIT = 5


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


async def add_correction(
    session: AsyncSession,
    user: User,
    rule_tag: RuleTag,
    *,
    original: str = "go",
    suggestion: str = "went",
    when: datetime = NOW,
) -> None:
    """A saved text with exactly one correction, so tests can shape a precise error history."""
    analyzed = AnalyzedText(
        user_id=user.id,
        original_text=original,
        corrected_text=suggestion,
        cefr_level=CefrLevel.A2,
        word_count=1,
        summary="x",
        ui_language_used=UiLanguage.EN,
        model_name="fake",
        created_at=when,
        corrections=[
            Correction(
                start_offset=0,
                end_offset=len(original),
                original_fragment=original,
                suggestion=suggestion,
                category=Category.GRAMMAR,
                rule_tag=rule_tag,
                explanation="x",
                position=0,
            )
        ],
    )
    session.add(analyzed)
    await session.commit()


class RecordingClient(FakeLLMClient):
    """Wraps the fake client to count and inspect the calls it received."""

    def __init__(self) -> None:
        self.calls: list[Any] = []

    async def generate_exercises(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return await super().generate_exercises(**kwargs)


class FailingClient:
    model_name = "failing"

    def __init__(self, error: Exception) -> None:
        self.error = error

    async def analyze_text(self, **_: object) -> dict[str, Any]:
        raise NotImplementedError  # unused here: only to satisfy the LLMClient protocol

    async def generate_exercises(self, **_: object) -> dict[str, Any]:
        raise self.error


async def generation_counter(session: AsyncSession, user_id: Any) -> int:
    value = await session.scalar(
        select(UsageCounter.generations_count).where(UsageCounter.user_id == user_id)
    )
    return value or 0


async def exercise_count(session: AsyncSession) -> int:
    return await session.scalar(select(func.count()).select_from(Exercise)) or 0


# --- top_rule_failures ---------------------------------------------------------------------------


async def test_ranks_rules_by_how_often_they_were_missed(session: AsyncSession, user: User) -> None:
    for _ in range(3):
        await add_correction(session, user, RuleTag.VERB_TENSE)
    for _ in range(2):
        await add_correction(session, user, RuleTag.ARTICLES)
    await add_correction(session, user, RuleTag.SPELLING_COMMON)

    failures = await top_rule_failures(session, user.id, since=NOW - timedelta(days=1))

    assert [f.rule_tag for f in failures] == [
        RuleTag.VERB_TENSE,
        RuleTag.ARTICLES,
        RuleTag.SPELLING_COMMON,
    ]


async def test_ties_are_broken_by_rule_name_so_the_order_is_stable(
    session: AsyncSession, user: User
) -> None:
    await add_correction(session, user, RuleTag.SPELLING_COMMON)
    await add_correction(session, user, RuleTag.ARTICLES)

    failures = await top_rule_failures(session, user.id, since=NOW - timedelta(days=1))

    assert [f.rule_tag for f in failures] == [RuleTag.ARTICLES, RuleTag.SPELLING_COMMON]


async def test_only_the_top_three_rules_are_returned(session: AsyncSession, user: User) -> None:
    for tag in (
        RuleTag.VERB_TENSE,
        RuleTag.ARTICLES,
        RuleTag.SPELLING_COMMON,
        RuleTag.PREPOSITIONS,
    ):
        await add_correction(session, user, tag)

    failures = await top_rule_failures(session, user.id, since=NOW - timedelta(days=1))

    assert len(failures) == 3


async def test_examples_are_the_users_own_real_mistakes_newest_first(
    session: AsyncSession, user: User
) -> None:
    await add_correction(
        session, user, RuleTag.VERB_TENSE, original="go", suggestion="went", when=NOW
    )
    await add_correction(
        session,
        user,
        RuleTag.VERB_TENSE,
        original="goed",
        suggestion="went",
        when=NOW + timedelta(hours=1),
    )

    [failure] = await top_rule_failures(session, user.id, since=NOW - timedelta(days=1))

    assert failure.examples == (("goed", "went"), ("go", "went"))


async def test_mistakes_outside_the_lookback_window_are_ignored(
    session: AsyncSession, user: User
) -> None:
    await add_correction(session, user, RuleTag.VERB_TENSE, when=NOW - timedelta(days=40))

    failures = await top_rule_failures(session, user.id, since=NOW - timedelta(days=30))

    assert failures == []


async def test_a_user_with_no_history_has_no_rule_failures(
    session: AsyncSession, user: User
) -> None:
    assert await top_rule_failures(session, user.id) == []


async def test_never_mixes_in_another_users_mistakes(session: AsyncSession, user: User) -> None:
    other = User(email="ben@example.com", password_hash="x", display_name="Ben")
    session.add(other)
    await session.commit()
    await add_correction(session, other, RuleTag.VERB_TENSE)

    assert await top_rule_failures(session, user.id, since=NOW - timedelta(days=1)) == []


# --- generate_or_reuse -----------------------------------------------------------------------


async def test_generates_nothing_and_spends_no_quota_without_recent_history(
    session: AsyncSession, user: User
) -> None:
    client = RecordingClient()

    result = await generate_or_reuse(session, client, user=user, daily_limit=LIMIT)

    assert result == []
    assert client.calls == []
    assert await generation_counter(session, user.id) == 0


async def test_generates_a_fresh_batch_from_the_users_history(
    session: AsyncSession, user: User
) -> None:
    await add_correction(session, user, RuleTag.VERB_TENSE)
    client = RecordingClient()

    result = await generate_or_reuse(session, client, user=user, daily_limit=LIMIT)

    assert len(result) > 0
    assert all(item.status is ExerciseStatus.PENDING for item in result)
    assert all(item.source_text_id is None for item in result)
    assert all(item.id is not None and item.created_at is not None for item in result)
    assert len(client.calls) == 1
    assert await generation_counter(session, user.id) == 1


async def test_reuses_pending_exercises_without_calling_the_model_again(
    session: AsyncSession, user: User
) -> None:
    await add_correction(session, user, RuleTag.VERB_TENSE)
    client = RecordingClient()
    first = await generate_or_reuse(session, client, user=user, daily_limit=LIMIT)

    second = await generate_or_reuse(session, client, user=user, daily_limit=LIMIT)

    assert [item.id for item in second] == [item.id for item in first]
    assert len(client.calls) == 1  # the model was asked only once
    assert await generation_counter(session, user.id) == 1  # reuse costs nothing


async def test_a_fresh_batch_is_generated_once_the_previous_one_is_all_done(
    session: AsyncSession, user: User
) -> None:
    await add_correction(session, user, RuleTag.VERB_TENSE)
    client = RecordingClient()
    first = await generate_or_reuse(session, client, user=user, daily_limit=LIMIT)
    for exercise in first:
        await attempt(
            session, user_id=user.id, exercise_id=exercise.id, user_answer=exercise.correct_answer
        )

    second = await generate_or_reuse(session, client, user=user, daily_limit=LIMIT)

    assert {item.id for item in second}.isdisjoint({item.id for item in first})
    assert len(client.calls) == 2
    assert await generation_counter(session, user.id) == 2


async def test_the_daily_generation_limit_is_enforced(session: AsyncSession, user: User) -> None:
    await add_correction(session, user, RuleTag.VERB_TENSE)
    client = RecordingClient()
    first = await generate_or_reuse(session, client, user=user, daily_limit=1)
    for exercise in first:
        await attempt(
            session, user_id=user.id, exercise_id=exercise.id, user_answer=exercise.correct_answer
        )

    with pytest.raises(AppError) as raised:
        await generate_or_reuse(session, client, user=user, daily_limit=1)

    assert raised.value.code == "daily_quota_exceeded"
    assert len(client.calls) == 1  # the model was never asked a second time


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (LLMUnavailableError(), "llm_unavailable"),
        (LLMInvalidResponseError("bad"), "llm_invalid_response"),
    ],
)
async def test_a_failed_generation_is_refunded_and_nothing_is_saved(
    session: AsyncSession, user: User, error: Exception, code: str
) -> None:
    await add_correction(session, user, RuleTag.VERB_TENSE)

    with pytest.raises(AppError) as raised:
        await generate_or_reuse(session, FailingClient(error), user=user, daily_limit=LIMIT)

    assert raised.value.code == code
    assert await generation_counter(session, user.id) == 0
    assert await exercise_count(session) == 0

    # the refund actually restored the allowance: a real client can still generate afterwards
    result = await generate_or_reuse(session, RecordingClient(), user=user, daily_limit=1)
    assert len(result) > 0


# --- attempt ---------------------------------------------------------------------------------


async def make_exercise(session: AsyncSession, user: User) -> Exercise:
    """One pending exercise: a whole batch is generated, only the first is returned."""
    await add_correction(session, user, RuleTag.VERB_TENSE)
    batch = await generate_or_reuse(session, FakeLLMClient(), user=user, daily_limit=LIMIT)
    return batch[0]


async def test_a_correct_answer_is_graded_and_explained(session: AsyncSession, user: User) -> None:
    exercise = await make_exercise(session, user)

    result = await attempt(
        session, user_id=user.id, exercise_id=exercise.id, user_answer=exercise.correct_answer
    )

    assert result.is_correct is True
    assert result.correct_answer == exercise.correct_answer
    assert result.explanation == exercise.explanation


async def test_an_incorrect_answer_is_graded_but_still_explained(
    session: AsyncSession, user: User
) -> None:
    exercise = await make_exercise(session, user)

    result = await attempt(
        session, user_id=user.id, exercise_id=exercise.id, user_answer="definitely not it"
    )

    assert result.is_correct is False
    assert result.explanation == exercise.explanation


async def test_grading_ignores_case_and_surrounding_whitespace(
    session: AsyncSession, user: User
) -> None:
    exercise = await make_exercise(session, user)
    padded = f"  {exercise.correct_answer.upper()}  "

    result = await attempt(session, user_id=user.id, exercise_id=exercise.id, user_answer=padded)

    assert result.is_correct is True


async def test_answering_marks_the_exercise_done(session: AsyncSession, user: User) -> None:
    exercise = await make_exercise(session, user)

    await attempt(session, user_id=user.id, exercise_id=exercise.id, user_answer="anything")

    refreshed = await session.get(Exercise, exercise.id)
    assert refreshed is not None
    assert refreshed.status is ExerciseStatus.DONE


async def test_an_exercise_cannot_be_answered_twice(session: AsyncSession, user: User) -> None:
    exercise = await make_exercise(session, user)
    await attempt(
        session, user_id=user.id, exercise_id=exercise.id, user_answer=exercise.correct_answer
    )

    with pytest.raises(AppError) as raised:
        await attempt(session, user_id=user.id, exercise_id=exercise.id, user_answer="second try")

    assert raised.value.code == ALREADY_ATTEMPTED
    assert raised.value.status_code == 409


async def test_attempting_a_missing_exercise_is_not_found(
    session: AsyncSession, user: User
) -> None:
    with pytest.raises(AppError) as raised:
        await attempt(session, user_id=user.id, exercise_id=uuid.uuid4(), user_answer="x")

    assert raised.value.code == "not_found"
    assert raised.value.status_code == 404


async def test_a_user_cannot_attempt_someone_elses_exercise(
    session: AsyncSession, user: User
) -> None:
    exercise = await make_exercise(session, user)
    other = User(email="ben@example.com", password_hash="x", display_name="Ben")
    session.add(other)
    await session.commit()

    with pytest.raises(AppError) as raised:
        await attempt(session, user_id=other.id, exercise_id=exercise.id, user_answer="x")

    assert raised.value.code == "not_found"
