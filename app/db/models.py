import enum
import uuid
from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class UiLanguage(enum.StrEnum):
    CA = "ca"
    ES = "es"
    EN = "en"
    FR = "fr"


class ThemePreference(enum.StrEnum):
    LIGHT = "light"
    DARK = "dark"
    SYSTEM = "system"


class TargetLevel(enum.StrEnum):
    """Level a user aims for. A1 is excluded: the app targets learners from A2 to C2."""

    A2 = "A2"
    B1 = "B1"
    B2 = "B2"
    C1 = "C1"
    C2 = "C2"


class CefrLevel(enum.StrEnum):
    """Estimated level of a text (Common European Framework of Reference)."""

    A1 = "A1"
    A2 = "A2"
    B1 = "B1"
    B2 = "B2"
    C1 = "C1"
    C2 = "C2"


class Category(enum.StrEnum):
    GRAMMAR = "grammar"
    SPELLING = "spelling"
    VOCABULARY = "vocabulary"
    PUNCTUATION = "punctuation"
    STYLE = "style"


class RuleTag(enum.StrEnum):
    """Closed list of error rules: it is what makes per-rule statistics possible."""

    VERB_TENSE = "verb_tense"
    SUBJECT_VERB_AGREEMENT = "subject_verb_agreement"
    ARTICLES = "articles"
    PREPOSITIONS = "prepositions"
    WORD_ORDER = "word_order"
    PLURAL_NOUNS = "plural_nouns"
    PRONOUNS = "pronouns"
    MODAL_VERBS = "modal_verbs"
    CONDITIONALS = "conditionals"
    PASSIVE_VOICE = "passive_voice"
    PHRASAL_VERBS = "phrasal_verbs"
    COLLOCATIONS = "collocations"
    FALSE_FRIENDS = "false_friends"
    SPELLING_COMMON = "spelling_common"
    PUNCTUATION_COMMAS = "punctuation_commas"
    CAPITALIZATION = "capitalization"
    REGISTER_FORMAL = "register_formal"
    RUN_ON_SENTENCE = "run_on_sentence"
    OTHER = "other"


class ExerciseType(enum.StrEnum):
    MULTIPLE_CHOICE = "multiple_choice"
    FILL_BLANK = "fill_blank"


class ExerciseStatus(enum.StrEnum):
    """`pending`: not attempted yet, offered on /practice. `done`: answered once, kept for the
    session summary and the stats, never asked again."""

    PENDING = "pending"
    DONE = "done"


def _text_enum[E: enum.StrEnum](enum_class: type[E], name: str, length: int = 16) -> Enum:
    """Store enums as VARCHAR + CHECK constraint instead of native PostgreSQL enum types.

    Native enums are painful to alter in migrations (ALTER TYPE cannot run in a transaction
    block on older versions and values cannot be removed); a CHECK constraint is trivial to change.
    """
    return Enum(
        enum_class,
        name=name,
        native_enum=False,
        create_constraint=True,
        length=length,
        values_callable=lambda cls: [member.value for member in cls],
    )


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    # Always stored lowercase (normalised at the API boundary); the unique index enforces it.
    email: Mapped[str] = mapped_column(String(320), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    display_name: Mapped[str] = mapped_column(String(80))
    ui_language: Mapped[UiLanguage] = mapped_column(
        _text_enum(UiLanguage, "ui_language"), default=UiLanguage.ES
    )
    theme_preference: Mapped[ThemePreference] = mapped_column(
        _text_enum(ThemePreference, "theme_preference"), default=ThemePreference.SYSTEM
    )
    target_level: Mapped[TargetLevel | None] = mapped_column(
        _text_enum(TargetLevel, "target_level"), default=None
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AnalyzedText(Base):
    """A text submitted by a user, with its AI analysis (table `texts`)."""

    __tablename__ = "texts"
    __table_args__ = (
        # The history is always "this user's texts, newest first".
        Index("ix_texts_user_id_created_at", "user_id", text("created_at DESC")),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    # ON DELETE CASCADE: deleting the account deletes all of the user's data.
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    title: Mapped[str | None] = mapped_column(String(200), default=None)
    # Cleaned text (normalised newlines, trimmed). Correction offsets refer to this exact string.
    original_text: Mapped[str] = mapped_column(Text)
    corrected_text: Mapped[str] = mapped_column(Text)
    cefr_level: Mapped[CefrLevel] = mapped_column(_text_enum(CefrLevel, "cefr_level"))
    word_count: Mapped[int] = mapped_column(Integer)
    summary: Mapped[str] = mapped_column(Text)
    ui_language_used: Mapped[UiLanguage] = mapped_column(_text_enum(UiLanguage, "ui_language_used"))
    model_name: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # selectin: async code cannot lazy-load, so corrections are fetched together with the text.
    corrections: Mapped[list["Correction"]] = relationship(
        back_populates="text",
        order_by="Correction.position",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="selectin",
    )


class Correction(Base):
    __tablename__ = "corrections"
    __table_args__ = (
        CheckConstraint("start_offset >= 0 AND end_offset > start_offset", name="valid_range"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    text_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("texts.id", ondelete="CASCADE"), index=True
    )
    # Offsets are in Unicode code points (Python string indices) and end-exclusive.
    start_offset: Mapped[int] = mapped_column(Integer)
    end_offset: Mapped[int] = mapped_column(Integer)
    original_fragment: Mapped[str] = mapped_column(Text)
    suggestion: Mapped[str] = mapped_column(Text)
    category: Mapped[Category] = mapped_column(_text_enum(Category, "category"))
    rule_tag: Mapped[RuleTag] = mapped_column(
        _text_enum(RuleTag, "rule_tag", length=32), index=True
    )
    explanation: Mapped[str] = mapped_column(Text)
    # Order of appearance in the text.
    position: Mapped[int] = mapped_column(Integer)

    text: Mapped[AnalyzedText] = relationship(back_populates="corrections")


class UsageCounter(Base):
    """Per-user, per-day usage, to enforce daily limits."""

    __tablename__ = "usage_counters"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    analyses_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    generations_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")


class GlobalUsageCounter(Base):
    """Every LLM call made by anyone, per day: caps the shared free-tier API key, independent of
    (and in addition to) each user's own daily limit. Never refunded: an attempt that reached the
    provider already spent real quota there, whether or not it then succeeded."""

    __tablename__ = "global_usage_counters"

    day: Mapped[date] = mapped_column(Date, primary_key=True)
    llm_calls: Mapped[int] = mapped_column(Integer, default=0, server_default="0")


class Exercise(Base):
    """A practice item, generated from the rules a user fails most in their last 30 days."""

    __tablename__ = "exercises"
    __table_args__ = (
        Index("ix_exercises_user_id_status", "user_id", "status"),
        # options is required for multiple_choice and must stay empty for fill_blank, so the
        # question type and its shape can never drift apart.
        CheckConstraint(
            "(type = 'multiple_choice' AND options IS NOT NULL) "
            "OR (type = 'fill_blank' AND options IS NULL)",
            name="options_match_type",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    # The text that prompted it, if any single one did; kept only to let the UI link back to it.
    # ON DELETE SET NULL: deleting that text must not delete the exercise built from it.
    source_text_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("texts.id", ondelete="SET NULL"), default=None
    )
    rule_tag: Mapped[RuleTag] = mapped_column(_text_enum(RuleTag, "exercise_rule_tag", length=32))
    type: Mapped[ExerciseType] = mapped_column(_text_enum(ExerciseType, "exercise_type"))
    prompt: Mapped[str] = mapped_column(Text)
    # The choices, in order, for multiple_choice; null for fill_blank (see options_match_type).
    # none_as_null: without it, a Python None is stored as the JSON scalar `null`, which is NOT
    # SQL NULL (`options IS NULL` would then be false), silently defeating that CHECK constraint.
    options: Mapped[list[str] | None] = mapped_column(JSONB(none_as_null=True), default=None)
    correct_answer: Mapped[str] = mapped_column(Text)
    explanation: Mapped[str] = mapped_column(Text)
    status: Mapped[ExerciseStatus] = mapped_column(
        _text_enum(ExerciseStatus, "exercise_status"), default=ExerciseStatus.PENDING
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ExerciseAttempt(Base):
    """One answer to one exercise. An exercise is attempted at most once (see Exercise.status)."""

    __tablename__ = "exercise_attempts"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    exercise_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("exercises.id", ondelete="CASCADE"), unique=True
    )
    # Denormalised on purpose: kept even if the exercise itself were ever removed some other way,
    # and it is what makes "delete my account" a single, simple filter.
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    user_answer: Mapped[str] = mapped_column(Text)
    is_correct: Mapped[bool] = mapped_column(Boolean)
    attempted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
