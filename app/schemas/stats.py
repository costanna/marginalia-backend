from datetime import date

from pydantic import BaseModel, ConfigDict

from app.db.models import Category, CefrLevel, RuleTag


class OverviewStats(BaseModel):
    """The progress screen's headline KPIs."""

    texts_count: int
    words_count: int
    # Rounded to one decimal: a KPI, not a precise scientific figure.
    errors_per_100_words: float
    # The level of the most recently analysed text; null until the user has written anything.
    current_level: CefrLevel | None
    # Consecutive days (today or yesterday, back through unbroken days) with at least one text.
    streak_days: int


class ProgressPoint(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    day: date
    errors_per_100_words: float
    word_count: int


class CategoryCount(BaseModel):
    """Always one row per category, zero-filled: a donut chart needs every slice, even empty."""

    category: Category
    count: int


class RuleCount(BaseModel):
    rule_tag: RuleTag
    count: int
