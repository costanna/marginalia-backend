from fastapi import APIRouter, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.api.v1.deps import SessionDep
from app.core.errors import AppError
from app.core.security import create_access_token, hash_password, verify_password
from app.db.models import User
from app.schemas.auth import LoginRequest, RegisterRequest, TokenResponse

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(payload: RegisterRequest, session: SessionDep) -> TokenResponse:
    user = User(
        email=payload.email,
        password_hash=hash_password(payload.password),
        display_name=payload.display_name,
        ui_language=payload.ui_language,
    )
    session.add(user)
    try:
        await session.commit()
    except IntegrityError:
        # Rely on the unique index rather than a "SELECT then INSERT" check, which two
        # concurrent requests could both pass.
        await session.rollback()
        raise AppError(
            code="email_taken",
            message="An account with this email already exists.",
            status_code=status.HTTP_409_CONFLICT,
        ) from None
    return TokenResponse(access_token=create_access_token(user.id))


@router.post("/login", response_model=TokenResponse)
async def login(payload: LoginRequest, session: SessionDep) -> TokenResponse:
    user = await session.scalar(select(User).where(User.email == payload.email))
    # Same error and similar timing whether the email is unknown or the password is wrong,
    # so the endpoint cannot be used to discover which emails are registered.
    if not verify_password(payload.password, user.password_hash if user else None) or user is None:
        raise AppError(
            code="invalid_credentials",
            message="Incorrect email or password.",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )
    return TokenResponse(access_token=create_access_token(user.id))
