from collections.abc import AsyncIterator
from datetime import date
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.errors import AppError
from app.db.models import AnalyzedText, TargetLevel, UiLanguage, UsageCounter, User
from app.services import usage
from app.services.analysis import analyze_and_save
from app.services.llm.base import LLMInvalidResponseError, LLMUnavailableError
from app.services.llm.fake_client import FakeLLMClient

TEXT = "Yesterday I go to the cinema with my friends."
LIMIT = 2
GLOBAL_LIMIT = 1000  # generous: only the dedicated global-limit tests set it low


class RecordingClient(FakeLLMClient):
    def __init__(self) -> None:
        self.target_levels: list[TargetLevel | None] = []

    async def analyze_text(self, **kwargs: Any) -> dict[str, Any]:
        self.target_levels.append(kwargs["target_level"])
        return await super().analyze_text(**kwargs)


class FailingClient:
    model_name = "failing"

    def __init__(self, error: Exception) -> None:
        self.error = error

    async def analyze_text(self, **_: object) -> dict[str, Any]:
        raise self.error


@pytest.fixture
async def session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    async with async_sessionmaker(engine, expire_on_commit=False)() as db_session:
        yield db_session


@pytest.fixture
async def user(session: AsyncSession) -> User:
    new_user = User(
        email="ana@example.com",
        password_hash="x",
        display_name="Ana",
        target_level=TargetLevel.B2,
    )
    session.add(new_user)
    await session.commit()
    return new_user


async def save(
    session: AsyncSession,
    user: User,
    client: Any,
    text: str = TEXT,
    global_limit: int = GLOBAL_LIMIT,
) -> AnalyzedText:
    return await analyze_and_save(
        session,
        client,
        user=user,
        text=text,
        title="My weekend",
        ui_language=UiLanguage.ES,
        max_chars=3000,
        daily_limit=LIMIT,
        global_limit=global_limit,
    )


async def used(session: AsyncSession) -> int:
    return (
        await session.scalar(select(func.coalesce(func.sum(UsageCounter.analyses_count), 0))) or 0
    )


async def stored_texts(session: AsyncSession) -> int:
    return await session.scalar(select(func.count()).select_from(AnalyzedText)) or 0


async def test_the_analysis_is_saved_with_its_corrections(
    session: AsyncSession, user: User
) -> None:
    saved = await save(session, user, FakeLLMClient())

    assert saved.id is not None and saved.created_at is not None
    assert saved.title == "My weekend"
    assert saved.original_text == TEXT
    assert saved.corrected_text == "Yesterday I went to the cinema with my friends."
    assert saved.word_count == 9
    assert saved.ui_language_used is UiLanguage.ES
    assert saved.model_name == "fake-llm"
    [correction] = saved.corrections
    assert (correction.start_offset, correction.end_offset, correction.position) == (12, 14, 0)
    assert await used(session) == 1


async def test_the_users_target_level_is_passed_to_the_model(
    session: AsyncSession, user: User
) -> None:
    client = RecordingClient()

    await save(session, user, client)

    assert client.target_levels == [TargetLevel.B2]


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (LLMUnavailableError(), "llm_unavailable"),
        (LLMInvalidResponseError("bad"), "llm_invalid_response"),
    ],
)
async def test_a_failed_analysis_is_refunded_and_nothing_is_saved(
    session: AsyncSession, user: User, error: Exception, code: str
) -> None:
    with pytest.raises(AppError) as raised:
        await save(session, user, FailingClient(error))

    assert raised.value.code == code
    assert await used(session) == 0
    assert await stored_texts(session) == 0


async def test_a_rejected_text_does_not_touch_the_allowance(
    session: AsyncSession, user: User
) -> None:
    client = RecordingClient()

    with pytest.raises(AppError) as raised:
        await save(session, user, client, text="too short")

    assert raised.value.code == "text_too_short"
    assert client.target_levels == []  # the LLM was never called
    assert await used(session) == 0


async def test_the_daily_limit_stops_the_analysis_before_calling_the_model(
    session: AsyncSession, user: User
) -> None:
    for _ in range(LIMIT):
        await save(session, user, FakeLLMClient())
    client = RecordingClient()

    with pytest.raises(AppError) as raised:
        await save(session, user, client)

    assert raised.value.code == "daily_quota_exceeded"
    assert client.target_levels == []
    assert await stored_texts(session) == LIMIT
    assert await used(session) == LIMIT


async def test_the_global_capacity_stops_the_analysis_and_refunds_the_users_allowance(
    session: AsyncSession, user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A day of its own: the global counter is shared by every test that reserves one, so a real
    # calendar day (like every other test here uses) would collide with all of them.
    monkeypatch.setattr(usage, "today", lambda: date(2099, 1, 1))
    await save(session, user, FakeLLMClient(), global_limit=1)  # spends the one shared slot
    client = RecordingClient()

    with pytest.raises(AppError) as raised:
        await save(session, user, client, global_limit=1)

    assert raised.value.code == "llm_capacity_reached"
    assert client.target_levels == []  # the model was never called
    assert await stored_texts(session) == 1
    assert await used(session) == 1  # the user's own allowance was refunded, not spent twice
