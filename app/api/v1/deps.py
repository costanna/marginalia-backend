from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.errors import AppError
from app.core.security import InvalidTokenError, decode_access_token
from app.db.models import User
from app.db.session import get_session
from app.services.llm import get_llm_client
from app.services.llm.base import LLMClient

# auto_error=False: a missing header would otherwise produce FastAPI's own 403 body;
# we raise our AppError so every 401 has the same shape.
_bearer = HTTPBearer(auto_error=False)

SessionDep = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
LLMClientDep = Annotated[LLMClient, Depends(get_llm_client)]


def _unauthorized() -> AppError:
    return AppError(
        code="unauthorized",
        message="Missing or invalid credentials.",
        status_code=401,
        headers={"WWW-Authenticate": "Bearer"},
    )


async def get_current_user(
    session: SessionDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> User:
    if credentials is None:
        raise _unauthorized()
    try:
        user_id = decode_access_token(credentials.credentials)
    except InvalidTokenError:
        raise _unauthorized() from None
    # A valid token for a deleted account must stop working: always re-check the user exists.
    user = await session.get(User, user_id)
    if user is None:
        raise _unauthorized()
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]
