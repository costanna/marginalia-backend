import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from app.db.models import ExerciseStatus, ExerciseType, RuleTag


class ExerciseRead(BaseModel):
    """One practice item. Never includes `correct_answer` or `explanation`: those are only
    revealed by POST /exercises/{id}/attempt, once the learner has actually answered."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    rule_tag: RuleTag
    type: ExerciseType
    prompt: str
    options: list[str] | None
    status: ExerciseStatus
    created_at: datetime


class ExerciseAttemptRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Not stripped here: comparison already ignores case and surrounding whitespace (see
    # app/services/exercises.py), and returning the answer verbatim in a future "review your
    # attempts" screen should show exactly what the learner typed.
    user_answer: Annotated[str, Field(min_length=1, max_length=500)]


class ExerciseAttemptResponse(BaseModel):
    """Immediate feedback for one attempt: whether it was right, and why."""

    model_config = ConfigDict(from_attributes=True)

    is_correct: bool
    correct_answer: str
    explanation: str


class ExerciseExport(ExerciseRead):
    """Used only by GET /me/export: unlike ExerciseRead, it is fine to reveal the answer here —
    this is the user's own data dump, not an active practice session."""

    correct_answer: str
    explanation: str


class ExerciseAttemptExport(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    exercise_id: uuid.UUID
    user_answer: str
    is_correct: bool
    attempted_at: datetime
