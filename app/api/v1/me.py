from datetime import UTC, datetime

from fastapi import APIRouter, Request, Response, status
from sqlalchemy import select

from app.api.v1.deps import CurrentUser, SessionDep
from app.core.rate_limit import limiter
from app.db.models import AnalyzedText, Exercise, ExerciseAttempt, UsageCounter, User
from app.schemas.exercises import ExerciseAttemptExport, ExerciseExport
from app.schemas.export import DataExport, UsageDayRead
from app.schemas.texts import TextRead
from app.schemas.user import UserRead, UserUpdate
from app.services.usage import today

router = APIRouter(prefix="/me", tags=["me"])

# Reads everything the user ever wrote, so it is limited much harder than a normal request.
EXPORT_RATE_LIMIT = "5/minute"


@router.get("", response_model=UserRead)
async def read_me(user: CurrentUser) -> User:
    return user


@router.patch("", response_model=UserRead)
async def update_me(payload: UserUpdate, user: CurrentUser, session: SessionDep) -> User:
    # model_fields_set holds only the fields the client actually sent, which lets an explicit
    # `"target_level": null` clear the value while an absent field is left untouched.
    # Sending null for the other (non-nullable) fields is ignored the same way.
    for field in payload.model_fields_set:
        value = getattr(payload, field)
        if value is not None or field == "target_level":
            setattr(user, field, value)
    await session.commit()
    return user


@router.get("/export", response_model=DataExport)
@limiter.limit(EXPORT_RATE_LIMIT)
async def export_my_data(
    request: Request, response: Response, user: CurrentUser, session: SessionDep
) -> DataExport:
    """All of the user's data as one JSON document, offered as a file download."""
    texts = await session.scalars(
        select(AnalyzedText)
        .where(AnalyzedText.user_id == user.id)
        .order_by(AnalyzedText.created_at.desc(), AnalyzedText.id.desc())
    )
    exercises = await session.scalars(
        select(Exercise).where(Exercise.user_id == user.id).order_by(Exercise.created_at)
    )
    attempts = await session.scalars(
        select(ExerciseAttempt)
        .where(ExerciseAttempt.user_id == user.id)
        .order_by(ExerciseAttempt.attempted_at)
    )
    usage = await session.scalars(
        select(UsageCounter).where(UsageCounter.user_id == user.id).order_by(UsageCounter.day)
    )
    response.headers["Content-Disposition"] = (
        f'attachment; filename="marginalia-export-{today().isoformat()}.json"'
    )
    return DataExport(
        exported_at=datetime.now(UTC),
        profile=UserRead.model_validate(user),
        texts=[TextRead.model_validate(text) for text in texts],
        exercises=[ExerciseExport.model_validate(item) for item in exercises],
        exercise_attempts=[ExerciseAttemptExport.model_validate(item) for item in attempts],
        usage=[UsageDayRead.model_validate(row) for row in usage],
    )


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
async def delete_me(user: CurrentUser, session: SessionDep) -> Response:
    # ON DELETE CASCADE on the child tables (added in later phases) removes the user's data.
    await session.delete(user)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
