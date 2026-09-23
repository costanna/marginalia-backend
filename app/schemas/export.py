from datetime import date, datetime

from pydantic import BaseModel, ConfigDict

from app.schemas.exercises import ExerciseAttemptExport, ExerciseExport
from app.schemas.texts import TextRead
from app.schemas.user import UserRead


class UsageDayRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    day: date
    analyses_count: int
    generations_count: int


class DataExport(BaseModel):
    """Everything the app stores about a user (GET /me/export).

    Built from the same schemas the rest of the API uses (a superset for exercises, which the
    active-practice endpoints deliberately keep the answer out of). When a later phase adds user
    data it has to be added here too: the export is only honest while it is complete.
    """

    exported_at: datetime
    profile: UserRead
    texts: list[TextRead]
    exercises: list[ExerciseExport]
    exercise_attempts: list[ExerciseAttemptExport]
    usage: list[UsageDayRead]
