import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.db.models import Category, CefrLevel, RuleTag, UiLanguage

# Hard cap on the raw request body. The real, configurable limit (MAX_TEXT_CHARS) is applied after
# cleaning and reported as text_too_long; this only stops absurdly large payloads early.
MAX_REQUEST_TEXT_CHARS = 20_000

Title = Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)]


class AnalyzeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: Annotated[str, Field(max_length=MAX_REQUEST_TEXT_CHARS)]
    title: Title | None = None
    # Language of the explanations. Defaults to the user's profile language.
    ui_language: UiLanguage | None = None


class CorrectionBase(BaseModel):
    """One correction. `start`/`end` are Unicode code point offsets into `original_text`
    (end-exclusive), i.e. Python string indices, NOT UTF-16 units as in JavaScript strings."""

    # populate_by_name + from_attributes: built from ORM rows (start_offset, ...) and from the
    # in-memory results of the analysis (start, ...) alike.
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    start: int = Field(validation_alias="start_offset")
    end: int = Field(validation_alias="end_offset")
    original: str = Field(validation_alias="original_fragment")
    suggestion: str
    category: Category
    rule_tag: RuleTag
    explanation: str


class CorrectionRead(CorrectionBase):
    id: uuid.UUID


class TextRead(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: uuid.UUID
    title: str | None
    original_text: str
    corrected_text: str
    cefr_level: CefrLevel
    word_count: int
    summary: str
    ui_language: UiLanguage = Field(validation_alias="ui_language_used")
    created_at: datetime
    corrections: list[CorrectionRead]


class TextSummary(BaseModel):
    """A row of the history list: no text bodies, no corrections."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str | None
    cefr_level: CefrLevel
    word_count: int
    corrections_count: int
    created_at: datetime


class TextPage(BaseModel):
    items: list[TextSummary]
    page: int
    page_size: int
    total: int


class DemoAnalyzeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: Annotated[str, Field(max_length=MAX_REQUEST_TEXT_CHARS)]
    ui_language: UiLanguage = UiLanguage.ES


class DemoAnalysisResponse(BaseModel):
    """Like TextRead, but nothing was saved: no id, no date, and corrections have no id."""

    original_text: str
    corrected_text: str
    cefr_level: CefrLevel
    word_count: int
    summary: str
    ui_language: UiLanguage
    corrections: list[CorrectionBase]
