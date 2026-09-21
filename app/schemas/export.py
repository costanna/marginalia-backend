from datetime import date, datetime

from pydantic import BaseModel, ConfigDict

from app.schemas.texts import TextRead
from app.schemas.user import UserRead


class UsageDayRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    day: date
    analyses_count: int
    generations_count: int


class DataExport(BaseModel):
    """Everything the app stores about a user (GET /me/export).

    Built from the same schemas the rest of the API uses, so it can never contain a field the
    API would not show (no password hash). When a later phase adds user data (exercises, attempts)
    it has to be added here too: the export is only honest while it is complete.
    """

    exported_at: datetime
    profile: UserRead
    texts: list[TextRead]
    usage: list[UsageDayRead]
