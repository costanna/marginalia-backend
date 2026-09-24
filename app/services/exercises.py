"""Practice: turn a user's most repeated mistakes into fresh exercises, and grade an attempt.

Generating exercises costs an LLM call, so a batch is only built once: while any exercise from an
earlier batch is still `pending`, /exercises/generate hands those back instead of asking the model
again (see `generate_or_reuse`). Answering one (`attempt`) marks it `done`, which is what lets the
next call generate a fresh batch.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.db.models import AnalyzedText, Correction, Exercise, ExerciseAttempt, ExerciseStatus, User
from app.services.llm.base import (
    LLMClient,
    LLMExerciseSet,
    LLMInvalidResponseError,
    LLMUnavailableError,
    RuleFailure,
)
from app.services.usage import refund_generation, reserve_generation, reserve_global_llm_call

# How many rules to build exercises for, and how many of the user's own mistakes to show the
# model as grounding for each (spec: "las 3 reglas más falladas").
TOP_RULES = 3
EXAMPLES_PER_RULE = 3
# A rule only counts if it was actually made recently: yesterday's grammar is worth practising,
# a mistake from months ago probably is not (spec: "errores de los últimos 30 días").
LOOKBACK_DAYS = 30
EXERCISES_PER_GENERATION = 6
LLM_ATTEMPTS = 2  # the first call plus a single retry when the answer is unusable

# A stable error code beyond the spec's list, for the one failure mode practice adds: answering
# the same exercise twice. Frontend and README both need to know about it.
ALREADY_ATTEMPTED = "exercise_already_attempted"


def _not_found() -> AppError:
    # Same answer for "does not exist" and "belongs to someone else": nothing leaks either way.
    return AppError(code="not_found", message="Exercise not found.", status_code=404)


def _normalize(answer: str) -> str:
    """Case- and whitespace-insensitive comparison: a correct "The" should not fail on "the "."""
    return answer.strip().casefold()


async def top_rule_failures(
    session: AsyncSession, user_id: uuid.UUID, *, since: datetime | None = None
) -> list[RuleFailure]:
    """The user's most-repeated rule mistakes in the lookback window, each with a few examples.

    Ordered by how often the rule was missed (ties broken by name, so results are stable). A rule
    with zero recent corrections never appears: nothing to build an exercise from.
    """
    since = since or datetime.now(UTC) - timedelta(days=LOOKBACK_DAYS)
    ranked = await session.execute(
        select(Correction.rule_tag, func.count(Correction.id).label("n"))
        .join(AnalyzedText, AnalyzedText.id == Correction.text_id)
        .where(AnalyzedText.user_id == user_id, AnalyzedText.created_at >= since)
        .group_by(Correction.rule_tag)
        .order_by(func.count(Correction.id).desc(), Correction.rule_tag)
        .limit(TOP_RULES)
    )
    top_tags = [row.rule_tag for row in ranked]

    failures = []
    for rule_tag in top_tags:
        examples = await session.execute(
            select(Correction.original_fragment, Correction.suggestion)
            .join(AnalyzedText, AnalyzedText.id == Correction.text_id)
            .where(
                AnalyzedText.user_id == user_id,
                AnalyzedText.created_at >= since,
                Correction.rule_tag == rule_tag,
            )
            .order_by(AnalyzedText.created_at.desc())
            .limit(EXAMPLES_PER_RULE)
        )
        failures.append(
            RuleFailure(
                rule_tag=rule_tag,
                examples=tuple((row.original_fragment, row.suggestion) for row in examples),
            )
        )
    return failures


async def _ask_llm_for_exercises(
    client: LLMClient, *, rule_failures: list[RuleFailure], user: User, count: int
) -> LLMExerciseSet:
    """Get a validated batch of exercises; retry once if the answer is unusable, then give up."""
    for _ in range(LLM_ATTEMPTS):
        try:
            raw = await client.generate_exercises(
                rule_failures=rule_failures, ui_language=user.ui_language, count=count
            )
            parsed = LLMExerciseSet.model_validate(raw)
            # An empty list is schema-valid, but a charged generation that yields nothing would
            # look like "you have nothing to practise" after spending a daily slot.
            if not parsed.exercises:
                continue
            return parsed
        except (LLMInvalidResponseError, ValidationError):
            continue
        except LLMUnavailableError:
            raise AppError(
                code="llm_unavailable",
                message="The exercise generator is temporarily unavailable.",
                status_code=503,
            ) from None
    raise AppError(
        code="llm_invalid_response",
        message="The exercise generator returned an unusable answer.",
        status_code=502,
    )


async def generate_or_reuse(
    session: AsyncSession, client: LLMClient, *, user: User, daily_limit: int, global_limit: int
) -> list[Exercise]:
    """Return this user's pending exercises, generating a fresh batch only if none are left.

    Nothing is charged for a plain reuse, and nothing is charged when there is nothing recent to
    build exercises from (an empty list, not an error: the practice screen then invites the user
    to write first).
    """
    # Captured once, up front: a commit or rollback expires the ORM object's attributes, and
    # re-reading user.id afterwards would need an async lazy-load that a plain attribute access
    # cannot perform (a real bug this used to have, surfaced by adding a second reservation step
    # whose own rollback expires user before the except block below ever reads user.id).
    user_id: uuid.UUID = user.id
    pending = await _pending_exercises(session, user_id)
    if pending:
        return pending

    rule_failures = await top_rule_failures(session, user_id)
    if not rule_failures:
        return []

    charged_day = await reserve_generation(session, user_id, daily_limit)
    try:
        await reserve_global_llm_call(session, global_limit)
    except Exception:
        await refund_generation(session, user_id, charged_day)
        raise
    try:
        answer = await _ask_llm_for_exercises(
            client, rule_failures=rule_failures, user=user, count=EXERCISES_PER_GENERATION
        )
        exercises = [
            Exercise(
                user_id=user_id,
                rule_tag=item.rule_tag,
                type=item.type,
                prompt=item.prompt,
                options=item.options,
                correct_answer=item.correct_answer,
                explanation=item.explanation,
            )
            for item in answer.exercises
        ]
        session.add_all(exercises)
        await session.commit()
        for exercise in exercises:
            # created_at is filled in by the database: load it now, async code cannot lazy-load.
            await session.refresh(exercise, attribute_names=["created_at"])
    except Exception:
        await session.rollback()
        await refund_generation(session, user_id, charged_day)
        raise
    return exercises


async def _pending_exercises(session: AsyncSession, user_id: uuid.UUID) -> list[Exercise]:
    result = await session.scalars(
        select(Exercise)
        .where(Exercise.user_id == user_id, Exercise.status == ExerciseStatus.PENDING)
        .order_by(Exercise.created_at)
    )
    return list(result)


@dataclass(frozen=True)
class AttemptResult:
    is_correct: bool
    correct_answer: str
    explanation: str


async def attempt(
    session: AsyncSession, *, user_id: uuid.UUID, exercise_id: uuid.UUID, user_answer: str
) -> AttemptResult:
    """Grade one answer, save it, and mark the exercise done (an exercise takes one attempt)."""
    exercise = await session.scalar(
        select(Exercise).where(Exercise.id == exercise_id, Exercise.user_id == user_id)
    )
    if exercise is None:
        raise _not_found()
    if exercise.status != ExerciseStatus.PENDING:
        raise AppError(
            code=ALREADY_ATTEMPTED,
            message="This exercise has already been answered.",
            status_code=409,
        )

    is_correct = _normalize(user_answer) == _normalize(exercise.correct_answer)
    exercise.status = ExerciseStatus.DONE
    session.add(
        ExerciseAttempt(
            exercise_id=exercise.id, user_id=user_id, user_answer=user_answer, is_correct=is_correct
        )
    )
    try:
        await session.commit()
    except IntegrityError:
        # Two tabs answering the same pending exercise: the unique index on exercise_id wins.
        await session.rollback()
        raise AppError(
            code=ALREADY_ATTEMPTED,
            message="This exercise has already been answered.",
            status_code=409,
        ) from None
    return AttemptResult(
        is_correct=is_correct,
        correct_answer=exercise.correct_answer,
        explanation=exercise.explanation,
    )
