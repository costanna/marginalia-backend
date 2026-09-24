from fastapi import APIRouter, Request

from app.api.v1.deps import LLMClientDep, SessionDep, SettingsDep
from app.core.config import get_settings
from app.core.rate_limit import limiter
from app.schemas.texts import CorrectionBase, DemoAnalysisResponse, DemoAnalyzeRequest
from app.services.analysis import analyze
from app.services.usage import reserve_global_llm_call

router = APIRouter(prefix="/demo", tags=["demo"])


@router.post("/analyze", response_model=DemoAnalysisResponse)
# The limit is read on every request so it follows DEMO_DAILY_LIMIT. Every attempt counts, even a
# failed one: this endpoint is public and calls a paid API.
@limiter.limit(lambda: f"{get_settings().demo_daily_limit}/day")
async def demo_analyze(
    request: Request,  # required by slowapi to find the client
    payload: DemoAnalyzeRequest,
    client: LLMClientDep,
    settings: SettingsDep,
    session: SessionDep,
) -> DemoAnalysisResponse:
    """Try the corrector without an account. Nothing is saved."""
    await reserve_global_llm_call(session, settings.daily_global_llm_limit)
    result = await analyze(
        client,
        text=payload.text,
        ui_language=payload.ui_language,
        target_level=None,
        max_chars=settings.max_text_chars,
    )
    return DemoAnalysisResponse(
        original_text=result.original_text,
        corrected_text=result.corrected_text,
        cefr_level=result.cefr_level,
        word_count=result.word_count,
        summary=result.summary,
        ui_language=payload.ui_language,
        corrections=[CorrectionBase.model_validate(item) for item in result.corrections],
    )
