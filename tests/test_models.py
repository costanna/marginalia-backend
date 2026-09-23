from datetime import date

import pytest
from sqlalchemy import func, insert, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.db.models import (
    AnalyzedText,
    Category,
    CefrLevel,
    Correction,
    Exercise,
    ExerciseAttempt,
    ExerciseType,
    RuleTag,
    UiLanguage,
    UsageCounter,
    User,
)


def make_user(email: str = "ana@example.com") -> User:
    return User(email=email, password_hash="x", display_name="Ana")


def make_text(user: User, *, corrections: int = 2) -> AnalyzedText:
    return AnalyzedText(
        user_id=user.id,
        original_text="Yesterday I go to the cinema.",
        corrected_text="Yesterday I went to the cinema.",
        cefr_level=CefrLevel.A2,
        word_count=6,
        summary="Good start.",
        ui_language_used=UiLanguage.EN,
        model_name="fake",
        corrections=[
            Correction(
                start_offset=12,
                end_offset=14,
                original_fragment="go",
                suggestion="went",
                category=Category.GRAMMAR,
                rule_tag=RuleTag.VERB_TENSE,
                explanation="Past simple.",
                position=i,
            )
            for i in range(corrections)
        ],
    )


def make_exercise(user: User, *, source_text_id: object = None) -> Exercise:
    return Exercise(
        user_id=user.id,
        source_text_id=source_text_id,
        rule_tag=RuleTag.VERB_TENSE,
        type=ExerciseType.FILL_BLANK,
        prompt="Yesterday I ___ home.",
        correct_answer="went",
        explanation="Past simple.",
    )


async def count(session: AsyncSession, table: str) -> int:
    return int((await session.execute(text(f"SELECT count(*) FROM {table}"))).scalar_one())


async def test_deleting_a_user_deletes_all_their_data(engine: AsyncEngine) -> None:
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        user = make_user()
        session.add(user)
        await session.flush()
        session.add(make_text(user))
        session.add(UsageCounter(user_id=user.id, day=date(2026, 9, 21), analyses_count=1))
        exercise = make_exercise(user)
        session.add(exercise)
        await session.flush()
        session.add(
            ExerciseAttempt(
                exercise_id=exercise.id, user_id=user.id, user_answer="went", is_correct=True
            )
        )
        await session.commit()
        assert (await count(session, "texts"), await count(session, "corrections")) == (1, 2)
        assert (await count(session, "exercises"), await count(session, "exercise_attempts")) == (
            1,
            1,
        )

        await session.delete(user)
        await session.commit()

        for table in (
            "users",
            "texts",
            "corrections",
            "usage_counters",
            "exercises",
            "exercise_attempts",
        ):
            assert await count(session, table) == 0, table


async def test_deleting_a_text_deletes_its_corrections_only(engine: AsyncEngine) -> None:
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        user = make_user()
        session.add(user)
        await session.flush()
        first, second = make_text(user), make_text(user)
        session.add_all([first, second])
        await session.commit()

        await session.delete(first)
        await session.commit()

        assert await count(session, "texts") == 1
        assert await count(session, "corrections") == 2
        assert await count(session, "users") == 1


async def test_corrections_are_loaded_with_the_text_in_position_order(
    engine: AsyncEngine,
) -> None:
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        user = make_user()
        session.add(user)
        await session.flush()
        analyzed = make_text(user, corrections=3)
        analyzed.corrections.reverse()  # inserted out of order on purpose
        session.add(analyzed)
        await session.commit()

    async with async_sessionmaker(engine)() as session:
        loaded = await session.scalar(select(AnalyzedText))
        assert loaded is not None
        assert [c.position for c in loaded.corrections] == [0, 1, 2]


async def test_database_rejects_an_empty_correction_range(engine: AsyncEngine) -> None:
    async with async_sessionmaker(engine)() as session:
        user = make_user()
        session.add(user)
        await session.flush()
        analyzed = make_text(user, corrections=1)
        analyzed.corrections[0].end_offset = analyzed.corrections[0].start_offset
        session.add(analyzed)
        with pytest.raises(IntegrityError):
            await session.commit()


async def test_usage_counter_is_unique_per_user_and_day(engine: AsyncEngine) -> None:
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        user = make_user()
        session.add(user)
        await session.flush()
        row = {"user_id": user.id, "day": date(2026, 9, 21)}
        # Core inserts bypass the ORM identity map, so the *database* constraint is what is tested.
        await session.execute(insert(UsageCounter).values(**row))
        with pytest.raises(IntegrityError):
            await session.execute(insert(UsageCounter).values(**row))


async def test_a_new_counter_starts_at_zero(engine: AsyncEngine) -> None:
    async with async_sessionmaker(engine)() as session:
        user = make_user()
        session.add(user)
        await session.flush()
        session.add(UsageCounter(user_id=user.id, day=date(2026, 9, 21)))
        await session.commit()

        total = await session.scalar(
            select(func.sum(UsageCounter.analyses_count + UsageCounter.generations_count))
        )
        assert total == 0


# --- Exercises and attempts -------------------------------------------------------------------


async def test_deleting_the_source_text_keeps_the_exercise_but_clears_the_reference(
    engine: AsyncEngine,
) -> None:
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        user = make_user()
        session.add(user)
        await session.flush()
        source = make_text(user, corrections=1)
        session.add(source)
        await session.flush()
        session.add(make_exercise(user, source_text_id=source.id))
        await session.commit()

        await session.delete(source)
        await session.commit()

    async with async_sessionmaker(engine)() as session:
        exercise = await session.scalar(select(Exercise))
        assert exercise is not None
        assert exercise.source_text_id is None


async def test_deleting_an_exercise_deletes_its_attempt_but_not_the_user(
    engine: AsyncEngine,
) -> None:
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        user = make_user()
        session.add(user)
        await session.flush()
        exercise = make_exercise(user)
        session.add(exercise)
        await session.flush()
        session.add(
            ExerciseAttempt(
                exercise_id=exercise.id, user_id=user.id, user_answer="went", is_correct=True
            )
        )
        await session.commit()

        await session.delete(exercise)
        await session.commit()

        assert await count(session, "exercise_attempts") == 0
        assert await count(session, "users") == 1


async def test_database_rejects_multiple_choice_without_options(engine: AsyncEngine) -> None:
    async with async_sessionmaker(engine)() as session:
        user = make_user()
        session.add(user)
        await session.flush()
        session.add(
            Exercise(
                user_id=user.id,
                rule_tag=RuleTag.ARTICLES,
                type=ExerciseType.MULTIPLE_CHOICE,
                prompt="She is ___ engineer.",
                options=None,
                correct_answer="an",
                explanation="Before a vowel sound, use 'an'.",
            )
        )
        with pytest.raises(IntegrityError):
            await session.commit()


async def test_database_rejects_fill_blank_with_options(engine: AsyncEngine) -> None:
    async with async_sessionmaker(engine)() as session:
        user = make_user()
        session.add(user)
        await session.flush()
        exercise = make_exercise(user)
        exercise.options = ["went", "goed"]
        session.add(exercise)
        with pytest.raises(IntegrityError):
            await session.commit()


async def test_an_exercise_can_only_be_attempted_once(engine: AsyncEngine) -> None:
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        user = make_user()
        session.add(user)
        await session.flush()
        exercise = make_exercise(user)
        session.add(exercise)
        await session.commit()

        row = {
            "exercise_id": exercise.id,
            "user_id": user.id,
            "user_answer": "went",
            "is_correct": True,
        }
        # Core inserts bypass the ORM identity map, so the *database* constraint is what is tested.
        await session.execute(insert(ExerciseAttempt).values(**row))
        with pytest.raises(IntegrityError):
            await session.execute(insert(ExerciseAttempt).values(**row))
