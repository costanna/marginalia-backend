import pytest
from pydantic import ValidationError

from app.db.models import Category, CefrLevel, RuleTag
from app.services.llm.base import MAX_CORRECTIONS, LLMAnalysis

CORRECTION = {
    "original": "go",
    "suggestion": "went",
    "category": "grammar",
    "rule_tag": "verb_tense",
    "explanation": "Past simple.",
}


def analysis(**overrides: object) -> dict[str, object]:
    return {"cefr_level": "A2", "summary": "Nice.", "corrections": [CORRECTION], **overrides}


def test_a_valid_answer_is_parsed_into_enums() -> None:
    result = LLMAnalysis.model_validate(analysis())

    assert result.cefr_level is CefrLevel.A2
    assert result.corrections[0].category is Category.GRAMMAR
    assert result.corrections[0].rule_tag is RuleTag.VERB_TENSE


def test_an_unknown_rule_tag_falls_back_to_other() -> None:
    made_up = {**CORRECTION, "rule_tag": "dangling_modifiers"}

    result = LLMAnalysis.model_validate(analysis(corrections=[made_up]))

    assert result.corrections[0].rule_tag is RuleTag.OTHER


@pytest.mark.parametrize(
    "bad",
    [
        {"cefr_level": "D9"},  # not a CEFR level
        {"corrections": [{**CORRECTION, "category": "vibes"}]},  # categories are strict
        {"corrections": [{**CORRECTION, "original": ""}]},  # nothing to locate
        {"corrections": [{"original": "go"}]},  # missing fields
        {"summary": None},
    ],
)
def test_invalid_answers_are_rejected(bad: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        LLMAnalysis.model_validate(analysis(**bad))


def test_more_than_the_maximum_corrections_are_truncated_not_rejected() -> None:
    many = [{**CORRECTION, "original": f"w{i}"} for i in range(MAX_CORRECTIONS + 10)]

    result = LLMAnalysis.model_validate(analysis(corrections=many))

    assert len(result.corrections) == MAX_CORRECTIONS
    assert result.corrections[0].original == "w0"


def test_an_empty_corrections_list_is_valid() -> None:
    assert LLMAnalysis.model_validate(analysis(corrections=[])).corrections == []
