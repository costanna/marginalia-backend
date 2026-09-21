import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints

from app.db.models import TargetLevel, ThemePreference, UiLanguage

DisplayName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    display_name: str
    ui_language: UiLanguage
    theme_preference: ThemePreference
    target_level: TargetLevel | None
    created_at: datetime


class UserUpdate(BaseModel):
    """Partial update (PATCH). Only the fields present in the request are changed.

    Email and password are deliberately not editable here.
    """

    model_config = ConfigDict(extra="forbid")

    display_name: DisplayName | None = None
    ui_language: UiLanguage | None = None
    theme_preference: ThemePreference | None = None
    # An explicit null clears the target level, so "absent" and "null" must stay
    # distinguishable (the endpoint uses model_fields_set).
    target_level: TargetLevel | None = None
