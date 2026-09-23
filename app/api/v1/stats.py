from typing import Annotated

from fastapi import APIRouter, Query

from app.api.v1.deps import CurrentUser, SessionDep
from app.schemas.stats import CategoryCount, OverviewStats, ProgressPoint, RuleCount
from app.services import stats

router = APIRouter(prefix="/stats", tags=["stats"])

Days = Annotated[int, Query(ge=1, le=365)]


@router.get("/overview", response_model=OverviewStats)
async def overview(user: CurrentUser, session: SessionDep) -> dict[str, object]:
    return await stats.overview(session, user.id)


@router.get("/progress", response_model=list[ProgressPoint])
async def progress(
    user: CurrentUser, session: SessionDep, days: Days = 90
) -> list[dict[str, object]]:
    return await stats.progress(session, user.id, days=days)


@router.get("/errors-by-category", response_model=list[CategoryCount])
async def errors_by_category(
    user: CurrentUser, session: SessionDep, days: Days = 30
) -> list[dict[str, object]]:
    return await stats.errors_by_category(session, user.id, days=days)


@router.get("/top-rules", response_model=list[RuleCount])
async def top_rules(
    user: CurrentUser, session: SessionDep, days: Days = 30
) -> list[dict[str, object]]:
    return await stats.top_rules(session, user.id, days=days)
