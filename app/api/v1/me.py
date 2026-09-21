from fastapi import APIRouter, Response, status

from app.api.v1.deps import CurrentUser, SessionDep
from app.db.models import User
from app.schemas.user import UserRead, UserUpdate

router = APIRouter(prefix="/me", tags=["me"])


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


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
async def delete_me(user: CurrentUser, session: SessionDep) -> Response:
    # ON DELETE CASCADE on the child tables (added in later phases) removes the user's data.
    await session.delete(user)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
