"""The contract between the application and any LLM provider.

The provider (Anthropic, a fake, ...) only *transports* a request and returns the raw JSON the
model produced. Validating it is the analysis service's job, so the "invalid answer -> retry once"
policy lives in one place instead of being reimplemented by every provider.
"""

from dataclasses import dataclass
from typing import Any, Protocol

from pydantic import BaseModel, Field, field_validator, model_validator

from app.db.models import Category, CefrLevel, ExerciseType, RuleTag, TargetLevel, UiLanguage

# The prompt asks for at most this many corrections; anything beyond is dropped, not an error.
MAX_CORRECTIONS = 25
# Same idea for a batch of exercises: a defensive cap, not the number actually requested.
MAX_EXERCISES = 12
# A fill-in-the-blank exercise marks its one gap with three underscores.
BLANK_MARKER = "___"


class LLMError(Exception):
    """Base class for provider failures."""


class LLMUnavailableError(LLMError):
    """The provider could not be reached or kept failing (network, timeout, 429, 5xx, auth)."""


class LLMInvalidResponseError(LLMError):
    """The provider answered, but not with usable JSON (truncated, refused, not JSON)."""


def _unknown_rule_becomes_other(value: object) -> object:
    """A rule outside the closed list falls back to `other` instead of failing the response.

    `other` exists precisely for errors the taxonomy does not cover, and rejecting the whole
    response over one label would cost a retry (and money) for a harmless deviation.
    """
    if isinstance(value, str) and value not in RuleTag:
        return RuleTag.OTHER
    return value


class LLMCorrection(BaseModel):
    original: str = Field(min_length=1)
    suggestion: str
    category: Category
    rule_tag: RuleTag
    explanation: str

    @field_validator("rule_tag", mode="before")
    @classmethod
    def unknown_rule_becomes_other(cls, value: object) -> object:
        return _unknown_rule_becomes_other(value)


class LLMAnalysis(BaseModel):
    cefr_level: CefrLevel
    summary: str
    corrections: list[LLMCorrection]

    @field_validator("corrections", mode="after")
    @classmethod
    def cap_corrections(cls, value: list[LLMCorrection]) -> list[LLMCorrection]:
        return value[:MAX_CORRECTIONS]


class LLMExercise(BaseModel):
    rule_tag: RuleTag
    type: ExerciseType
    prompt: str = Field(min_length=1)
    # Required (3-6 choices) for multiple_choice; must be absent for fill_blank, see check_shape.
    options: list[str] | None = None
    correct_answer: str = Field(min_length=1)
    explanation: str

    @field_validator("rule_tag", mode="before")
    @classmethod
    def unknown_rule_becomes_other(cls, value: object) -> object:
        return _unknown_rule_becomes_other(value)

    @model_validator(mode="after")
    def check_shape(self) -> "LLMExercise":
        """The two exercise types are validated as strictly as the database CHECK constraint
        (`options_match_type`): a shape mismatch here would otherwise surface as a 500 on insert."""
        if self.type is ExerciseType.MULTIPLE_CHOICE:
            if self.options is None or len(self.options) < 2:
                raise ValueError("multiple_choice needs at least 2 options")
            if self.correct_answer not in self.options:
                raise ValueError("correct_answer must be one of options")
        else:
            if self.options is not None:
                raise ValueError("fill_blank must not have options")
            if self.prompt.count(BLANK_MARKER) != 1:
                raise ValueError(f"fill_blank prompt must contain exactly one {BLANK_MARKER}")
        return self


class LLMExerciseSet(BaseModel):
    exercises: list[LLMExercise]

    @field_validator("exercises", mode="after")
    @classmethod
    def cap_exercises(cls, value: list[LLMExercise]) -> list[LLMExercise]:
        return value[:MAX_EXERCISES]


@dataclass(frozen=True)
class RuleFailure:
    """One rule the learner keeps failing, with a few of their own (original, suggestion) pairs.

    The examples are short fragments already stored as corrections, never a full text: enough to
    ground the exercise without sending more of the learner's writing than needed.
    """

    rule_tag: RuleTag
    examples: tuple[tuple[str, str], ...]


class LLMClient(Protocol):
    """What the analysis service needs from a provider."""

    model_name: str

    async def analyze_text(
        self,
        *,
        text: str,
        ui_language: UiLanguage,
        target_level: TargetLevel | None,
    ) -> dict[str, Any]:
        """Return the raw JSON object produced by the model.

        Raises LLMUnavailableError or LLMInvalidResponseError.
        """
        ...

    async def generate_exercises(
        self,
        *,
        rule_failures: list[RuleFailure],
        ui_language: UiLanguage,
        count: int,
    ) -> dict[str, Any]:
        """Return the raw JSON object produced by the model: `{"exercises": [...]}`.

        Raises LLMUnavailableError or LLMInvalidResponseError.
        """
        ...
