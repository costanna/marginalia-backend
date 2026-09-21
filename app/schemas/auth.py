from typing import Annotated

from pydantic import BaseModel, ConfigDict, EmailStr, Field, StringConstraints

from app.db.models import UiLanguage
from app.schemas.user import DisplayName

# Lowercase and trim so "Ana@Mail.com " and "ana@mail.com" are the same account.
NormalizedEmail = Annotated[EmailStr, StringConstraints(strip_whitespace=True, to_lower=True)]

# Argon2 handles long inputs, but an upper bound avoids feeding huge payloads to a costly hash.
Password = Annotated[str, Field(min_length=8, max_length=128)]


class RegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: NormalizedEmail
    password: Password
    display_name: DisplayName
    ui_language: UiLanguage = UiLanguage.ES


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: NormalizedEmail
    # No min_length here: login must not reveal password rules, only "invalid credentials".
    password: Annotated[str, Field(max_length=128)]


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
