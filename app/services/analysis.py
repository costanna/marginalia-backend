"""Text analysis: clean the text, ask the LLM, place its corrections, and save the result.

Everything here that does not need the database is a plain function so it can be tested without
one. Offsets are Unicode code points (Python string indices), end-exclusive.
"""

import re
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import date

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.db.models import (
    AnalyzedText,
    Category,
    CefrLevel,
    Correction,
    RuleTag,
    TargetLevel,
    UiLanguage,
    User,
)
from app.services.llm.base import (
    LLMAnalysis,
    LLMClient,
    LLMCorrection,
    LLMInvalidResponseError,
    LLMUnavailableError,
)
from app.services.usage import refund_analysis, reserve_analysis

MIN_TEXT_CHARS = 20
LLM_ATTEMPTS = 2  # the first call plus a single retry when the answer is unusable

_WORD = re.compile(r"[^\W_]+(?:['’\-][^\W_]+)*")


@dataclass(frozen=True)
class LocatedCorrection:
    start: int
    end: int
    original: str
    suggestion: str
    category: Category
    rule_tag: RuleTag
    explanation: str


@dataclass(frozen=True)
class AnalysisResult:
    original_text: str  # the cleaned text: every offset refers to exactly this string
    corrected_text: str
    cefr_level: CefrLevel
    summary: str
    word_count: int
    corrections: list[LocatedCorrection]


# --- Cleaning and validation -------------------------------------------------------------------


def clean_text(raw: str) -> str:
    """Normalise the text so the LLM, the database and the offsets all see the same string.

    - NFC: "é" typed as "e" + combining accent (common from some keyboards) becomes one
      character, otherwise the model's answer would not match the text literally.
    - CRLF / CR become LF, so line breaks count the same everywhere.
    - Control characters are dropped (keeping newline and tab). This matters: PostgreSQL cannot
      store a NUL character in a text column and would fail with a server error.
    """
    text = unicodedata.normalize("NFC", raw).replace("\r\n", "\n").replace("\r", "\n")
    text = "".join(ch for ch in text if ch in "\n\t" or unicodedata.category(ch) != "Cc")
    return text.strip()


def count_words(text: str) -> int:
    """Words, treating "don't" and "well-known" as one word each."""
    return len(_WORD.findall(text))


def validate_length(text: str, max_chars: int) -> None:
    length = len(text)
    if length < MIN_TEXT_CHARS:
        raise AppError(
            code="text_too_short",
            message=f"The text must have at least {MIN_TEXT_CHARS} characters.",
            status_code=422,
            details={"min": MIN_TEXT_CHARS, "max": max_chars, "length": length},
        )
    if length > max_chars:
        raise AppError(
            code="text_too_long",
            message=f"The text must have at most {max_chars} characters.",
            status_code=422,
            details={"min": MIN_TEXT_CHARS, "max": max_chars, "length": length},
        )


# --- Placing corrections in the text ------------------------------------------------------------


def _is_word_char(char: str) -> bool:
    return char.isalnum() or char == "_"


def _occurrences(text: str, fragment: str) -> list[tuple[int, int]]:
    """Every place where `fragment` appears as a whole word (or phrase) in `text`.

    Word boundaries matter: without them "go" would match inside "google" or "ago".
    """
    found = []
    start = text.find(fragment)
    while start != -1:
        end = start + len(fragment)
        starts_ok = not (
            _is_word_char(fragment[0]) and start > 0 and _is_word_char(text[start - 1])
        )
        ends_ok = not (_is_word_char(fragment[-1]) and end < len(text) and _is_word_char(text[end]))
        if starts_ok and ends_ok:
            found.append((start, end))
        start = text.find(fragment, start + 1)
    return found


def _overlaps(start: int, end: int, taken: list[tuple[int, int]]) -> bool:
    return any(start < other_end and other_start < end for other_start, other_end in taken)


def locate_corrections(text: str, corrections: list[LLMCorrection]) -> list[LocatedCorrection]:
    """Turn the model's corrections into positioned ones, sorted by position in the text.

    Language models are unreliable at counting characters, so the model only quotes the
    fragment and we find where it is. A correction is dropped when its fragment is not in the
    text, changes nothing, or would overlap one that was already accepted (the first one, in the
    model's order, wins). If a fragment appears several times, each entry takes the next free
    occurrence.
    """
    taken: list[tuple[int, int]] = []
    located: list[LocatedCorrection] = []
    for correction in corrections:
        if correction.suggestion == correction.original:
            continue
        for start, end in _occurrences(text, correction.original):
            if not _overlaps(start, end, taken):
                taken.append((start, end))
                located.append(
                    LocatedCorrection(
                        start=start,
                        end=end,
                        original=correction.original,
                        suggestion=correction.suggestion,
                        category=correction.category,
                        rule_tag=correction.rule_tag,
                        explanation=correction.explanation,
                    )
                )
                break
    return sorted(located, key=lambda item: item.start)


def apply_corrections(text: str, corrections: list[LocatedCorrection]) -> str:
    """Build the corrected text.

    Replacing from the END to the START keeps the positions of the not-yet-applied corrections
    valid: changing the tail never shifts what comes before it.
    """
    corrected = text
    for item in sorted(corrections, key=lambda c: c.start, reverse=True):
        corrected = corrected[: item.start] + item.suggestion + corrected[item.end :]
    return corrected


# --- Talking to the LLM -------------------------------------------------------------------------


async def ask_llm(
    client: LLMClient,
    *,
    text: str,
    ui_language: UiLanguage,
    target_level: TargetLevel | None,
) -> LLMAnalysis:
    """Get a validated analysis; retry once if the answer is unusable, then give up."""
    for _ in range(LLM_ATTEMPTS):
        try:
            raw = await client.analyze_text(
                text=text, ui_language=ui_language, target_level=target_level
            )
            return LLMAnalysis.model_validate(raw)
        except (LLMInvalidResponseError, ValidationError):
            continue
        except LLMUnavailableError:
            # Not retried here: the provider client already retried transient failures.
            raise AppError(
                code="llm_unavailable",
                message="The analysis service is temporarily unavailable.",
                status_code=503,
            ) from None
    raise AppError(
        code="llm_invalid_response",
        message="The analysis service returned an unusable answer.",
        status_code=502,
    )


async def analyze(
    client: LLMClient,
    *,
    text: str,
    ui_language: UiLanguage,
    target_level: TargetLevel | None,
    max_chars: int,
) -> AnalysisResult:
    """Analyse a text without touching the database (used by the demo and by saving)."""
    cleaned = clean_text(text)
    validate_length(cleaned, max_chars)
    answer = await ask_llm(client, text=cleaned, ui_language=ui_language, target_level=target_level)
    corrections = locate_corrections(cleaned, answer.corrections)
    return AnalysisResult(
        original_text=cleaned,
        corrected_text=apply_corrections(cleaned, corrections),
        cefr_level=answer.cefr_level,
        summary=answer.summary,
        word_count=count_words(cleaned),
        corrections=corrections,
    )


# --- Saving -------------------------------------------------------------------------------------


async def analyze_and_save(
    session: AsyncSession,
    client: LLMClient,
    *,
    user: User,
    text: str,
    title: str | None,
    ui_language: UiLanguage,
    max_chars: int,
    daily_limit: int,
) -> AnalyzedText:
    """Analyse a user's text, charge their daily allowance and store the result.

    The text is validated BEFORE reserving quota (a rejected text costs nothing) and the
    reservation is refunded if anything fails afterwards.
    """
    cleaned = clean_text(text)
    validate_length(cleaned, max_chars)

    user_id: uuid.UUID = user.id
    target_level = user.target_level
    charged_day: date = await reserve_analysis(session, user_id, daily_limit)
    try:
        result = await analyze(
            client,
            text=cleaned,
            ui_language=ui_language,
            target_level=target_level,
            max_chars=max_chars,
        )
        analyzed = AnalyzedText(
            user_id=user_id,
            title=title,
            original_text=result.original_text,
            corrected_text=result.corrected_text,
            cefr_level=result.cefr_level,
            word_count=result.word_count,
            summary=result.summary,
            ui_language_used=ui_language,
            model_name=client.model_name,
            corrections=[
                Correction(
                    start_offset=item.start,
                    end_offset=item.end,
                    original_fragment=item.original,
                    suggestion=item.suggestion,
                    category=item.category,
                    rule_tag=item.rule_tag,
                    explanation=item.explanation,
                    position=position,
                )
                for position, item in enumerate(result.corrections)
            ],
        )
        session.add(analyzed)
        await session.commit()
        # created_at is filled in by the database: load it now, async code cannot lazy-load later.
        await session.refresh(analyzed, attribute_names=["created_at"])
    except Exception:
        await session.rollback()
        await refund_analysis(session, user_id, charged_day)
        raise
    return analyzed
