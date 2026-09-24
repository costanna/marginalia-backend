import uuid
from typing import Annotated

from fastapi import APIRouter, Query
from fastapi import status as http_status
from sqlalchemy import select

from app.api.v1.deps import CurrentUser, LLMClientDep, SessionDep, SettingsDep
from app.db.models import Exercise, ExerciseStatus
from app.schemas.exercises import ExerciseAttemptRequest, ExerciseAttemptResponse, ExerciseRead
from app.services.exercises import attempt as attempt_exercise
from app.services.exercises import generate_or_reuse

router = APIRouter(prefix="/exercises", tags=["exercises"])


@router.post("/generate", response_model=list[ExerciseRead])
async def generate_exercises(
    user: CurrentUser, session: SessionDep, client: LLMClientDep, settings: SettingsDep
) -> list[Exercise]:
    """Generate fresh exercises from the rules the user fails most, or hand back pending ones."""
    return await generate_or_reuse(
        session,
        client,
        user=user,
        daily_limit=settings.daily_generation_limit,
        global_limit=settings.daily_global_llm_limit,
    )


@router.get("", response_model=list[ExerciseRead])
async def list_exercises(
    user: CurrentUser,
    session: SessionDep,
    status: Annotated[ExerciseStatus | None, Query()] = None,
) -> list[Exercise]:
    """The user's exercises, oldest first (the order a practice session works through them)."""
    conditions = [Exercise.user_id == user.id]
    if status is not None:
        conditions.append(Exercise.status == status)
    result = await session.scalars(
        select(Exercise).where(*conditions).order_by(Exercise.created_at)
    )
    return list(result)


@router.post(
    "/{exercise_id}/attempt",
    response_model=ExerciseAttemptResponse,
    status_code=http_status.HTTP_201_CREATED,
)
async def attempt(
    exercise_id: uuid.UUID,
    payload: ExerciseAttemptRequest,
    user: CurrentUser,
    session: SessionDep,
) -> ExerciseAttemptResponse:
    result = await attempt_exercise(
        session, user_id=user.id, exercise_id=exercise_id, user_answer=payload.user_answer
    )
    return ExerciseAttemptResponse.model_validate(result)
