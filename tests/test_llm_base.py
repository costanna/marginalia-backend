import pytest
from pydantic import ValidationError

from app.db.models import Category, CefrLevel, RuleTag
from app.services.llm.base import (
    MAX_CORRECTIONS,
    MAX_EXERCISES,
    LLMAnalysis,
    LLMExercise,
    LLMExerciseSet,
    RuleFailure,
)

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


# --- Exercises -------------------------------------------------------------------------------

MULTIPLE_CHOICE = {
    "rule_tag": "articles",
    "type": "multiple_choice",
    "prompt": "She is ___ engineer.",
    "options": ["a", "an", "the"],
    "correct_answer": "an",
    "explanation": "Before a vowel sound, use 'an'.",
}

FILL_BLANK = {
    "rule_tag": "verb_tense",
    "type": "fill_blank",
    "prompt": "Yesterday I ___ home.",
    "correct_answer": "went",
    "explanation": "Past simple.",
}


def test_a_valid_multiple_choice_exercise_is_parsed() -> None:
    exercise = LLMExercise.model_validate(MULTIPLE_CHOICE)

    assert exercise.rule_tag is RuleTag.ARTICLES
    assert exercise.options == ["a", "an", "the"]
    assert exercise.correct_answer == "an"


def test_a_valid_fill_blank_exercise_is_parsed() -> None:
    exercise = LLMExercise.model_validate(FILL_BLANK)

    assert exercise.type.value == "fill_blank"
    assert exercise.options is None


def test_an_unknown_rule_tag_falls_back_to_other_for_exercises_too() -> None:
    exercise = LLMExercise.model_validate({**FILL_BLANK, "rule_tag": "made_up_rule"})

    assert exercise.rule_tag is RuleTag.OTHER


@pytest.mark.parametrize(
    "bad",
    [
        {**MULTIPLE_CHOICE, "correct_answer": "the wrong one"},  # not in options
        {**MULTIPLE_CHOICE, "options": ["only-one"]},  # too few choices
        {**MULTIPLE_CHOICE, "options": None},  # multiple_choice needs options
        {**FILL_BLANK, "options": ["a", "b"]},  # fill_blank must not have options
        {**FILL_BLANK, "prompt": "No blank here."},  # missing the ___ marker
        {**FILL_BLANK, "prompt": "Two ___ blanks ___ here."},  # more than one
        {**FILL_BLANK, "prompt": ""},  # empty prompt
        {**FILL_BLANK, "correct_answer": ""},  # empty answer
        {**FILL_BLANK, "type": "essay"},  # not a real type
    ],
)
def test_invalid_exercises_are_rejected(bad: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        LLMExercise.model_validate(bad)


def test_more_than_the_maximum_exercises_are_truncated_not_rejected() -> None:
    many = [{**FILL_BLANK} for _ in range(MAX_EXERCISES + 5)]

    result = LLMExerciseSet.model_validate({"exercises": many})

    assert len(result.exercises) == MAX_EXERCISES


def test_an_empty_exercise_set_is_valid() -> None:
    assert LLMExerciseSet.model_validate({"exercises": []}).exercises == []


def test_rule_failure_holds_the_examples_that_ground_an_exercise() -> None:
    failure = RuleFailure(rule_tag=RuleTag.VERB_TENSE, examples=(("go", "went"),))

    assert failure.rule_tag is RuleTag.VERB_TENSE
    assert failure.examples == (("go", "went"),)
