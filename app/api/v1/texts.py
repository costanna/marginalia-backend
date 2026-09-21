import uuid
from typing import Annotated

from fastapi import APIRouter, Query, Response, status
from sqlalchemy import func, select

from app.api.v1.deps import CurrentUser, LLMClientDep, SessionDep, SettingsDep
from app.core.errors import AppError
from app.db.models import AnalyzedText, Correction
from app.schemas.texts import AnalyzeRequest, TextPage, TextRead, TextSummary
from app.services.analysis import analyze_and_save

router = APIRouter(prefix="/texts", tags=["texts"])

DEFAULT_PAGE_SIZE = 12  # a multiple of 3: it fills the 3-column grid of the history screen
MAX_PAGE_SIZE = 50


def _not_found() -> AppError:
    # Same answer for "does not exist" and "belongs to someone else": do not reveal which.
    return AppError(code="not_found", message="Text not found.", status_code=404)


async def _get_own_text(
    session: SessionDep, user_id: uuid.UUID, text_id: uuid.UUID
) -> AnalyzedText:
    found = await session.scalar(
        select(AnalyzedText).where(AnalyzedText.id == text_id, AnalyzedText.user_id == user_id)
    )
    if found is None:
        raise _not_found()
    return found


@router.post("/analyze", response_model=TextRead, status_code=status.HTTP_201_CREATED)
async def analyze_text(
    payload: AnalyzeRequest,
    user: CurrentUser,
    session: SessionDep,
    client: LLMClientDep,
    settings: SettingsDep,
) -> AnalyzedText:
    """Analyse a text, save it and return the corrections."""
    return await analyze_and_save(
        session,
        client,
        user=user,
        text=payload.text,
        title=payload.title or None,  # a blank title is stored as "no title"
        ui_language=payload.ui_language or user.ui_language,
        max_chars=settings.max_text_chars,
        daily_limit=settings.daily_analysis_limit,
    )


@router.get("", response_model=TextPage)
async def list_texts(
    user: CurrentUser,
    session: SessionDep,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
) -> TextPage:
    """The user's history, newest first."""
    total = await session.scalar(
        select(func.count()).select_from(AnalyzedText).where(AnalyzedText.user_id == user.id)
    )
    # Only the columns the list needs: selecting the entity would also load every correction.
    rows = await session.execute(
        select(
            AnalyzedText.id,
            AnalyzedText.title,
            AnalyzedText.cefr_level,
            AnalyzedText.word_count,
            AnalyzedText.created_at,
            func.count(Correction.id).label("corrections_count"),
        )
        .outerjoin(Correction, Correction.text_id == AnalyzedText.id)
        .where(AnalyzedText.user_id == user.id)
        .group_by(AnalyzedText.id)
        # id as tie-breaker keeps the order (and therefore the pages) stable.
        .order_by(AnalyzedText.created_at.desc(), AnalyzedText.id.desc())
        .limit(page_size)
        .offset((page - 1) * page_size)
    )
    return TextPage(
        items=[TextSummary.model_validate(row) for row in rows],
        page=page,
        page_size=page_size,
        total=total or 0,
    )


@router.get("/{text_id}", response_model=TextRead)
async def read_text(text_id: uuid.UUID, user: CurrentUser, session: SessionDep) -> AnalyzedText:
    return await _get_own_text(session, user.id, text_id)


@router.delete("/{text_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_text(text_id: uuid.UUID, user: CurrentUser, session: SessionDep) -> Response:
    found = await _get_own_text(session, user.id, text_id)
    await session.delete(found)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
