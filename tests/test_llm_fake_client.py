from app.db.models import Category, RuleTag, UiLanguage
from app.services.llm.base import LLMAnalysis, LLMExerciseSet, RuleFailure
from app.services.llm.fake_client import FakeLLMClient

CLIENT = FakeLLMClient()


async def analyze(text: str, language: UiLanguage = UiLanguage.EN) -> LLMAnalysis:
    raw = await CLIENT.analyze_text(text=text, ui_language=language, target_level=None)
    return LLMAnalysis.model_validate(raw)  # the fake must speak the real contract


async def test_finds_a_known_mistake() -> None:
    result = await analyze("Yesterday I go to the cinema with my friends.")

    assert [(c.original, c.suggestion) for c in result.corrections] == [("go", "went")]
    assert result.corrections[0].category is Category.GRAMMAR
    assert result.corrections[0].rule_tag is RuleTag.VERB_TENSE


async def test_clean_text_has_no_corrections() -> None:
    assert (await analyze("Yesterday I went to the cinema with my friends.")).corrections == []


async def test_corrections_come_in_text_order() -> None:
    result = await analyze("I recieve teh letter. I has a apple.")

    assert [c.original for c in result.corrections] == ["recieve", "teh", "I has", "a"]


async def test_explanation_and_summary_follow_the_ui_language() -> None:
    text = "Yesterday I go home."
    ca = await analyze(text, UiLanguage.CA)
    es = await analyze(text, UiLanguage.ES)

    assert "passat simple" in ca.corrections[0].explanation
    assert "pasado simple" in es.corrections[0].explanation
    assert ca.summary != es.summary


async def test_it_is_deterministic() -> None:
    text = "I has teh book."

    assert await analyze(text) == await analyze(text)


async def test_it_exposes_a_model_name() -> None:
    assert CLIENT.model_name == "fake-llm"


# --- generate_exercises -----------------------------------------------------------------------


async def generate(
    rule_failures: list[RuleFailure], count: int = 6, language: UiLanguage = UiLanguage.EN
) -> LLMExerciseSet:
    raw = await CLIENT.generate_exercises(
        rule_failures=rule_failures, ui_language=language, count=count
    )
    return LLMExerciseSet.model_validate(raw)  # the fake must speak the real contract too


async def test_generates_exactly_the_requested_count() -> None:
    result = await generate([RuleFailure(RuleTag.VERB_TENSE, ())], count=4)

    assert len(result.exercises) == 4


async def test_alternates_fill_blank_and_multiple_choice() -> None:
    result = await generate([RuleFailure(RuleTag.VERB_TENSE, ())], count=4)

    assert [item.type.value for item in result.exercises] == [
        "fill_blank",
        "multiple_choice",
        "fill_blank",
        "multiple_choice",
    ]


async def test_cycles_through_every_given_rule() -> None:
    failures = [
        RuleFailure(RuleTag.VERB_TENSE, ()),
        RuleFailure(RuleTag.ARTICLES, ()),
        RuleFailure(RuleTag.SPELLING_COMMON, ()),
    ]

    result = await generate(failures, count=6)

    assert [item.rule_tag for item in result.exercises] == [
        RuleTag.VERB_TENSE,
        RuleTag.ARTICLES,
        RuleTag.SPELLING_COMMON,
        RuleTag.VERB_TENSE,
        RuleTag.ARTICLES,
        RuleTag.SPELLING_COMMON,
    ]


async def test_a_rule_outside_the_small_catalogue_still_gets_a_valid_exercise() -> None:
    result = await generate([RuleFailure(RuleTag.PHRASAL_VERBS, ())], count=2)

    assert [item.rule_tag for item in result.exercises] == [RuleTag.PHRASAL_VERBS] * 2


async def test_falls_back_to_other_when_no_rule_is_given() -> None:
    result = await generate([], count=2)

    assert [item.rule_tag for item in result.exercises] == [RuleTag.OTHER] * 2


async def test_explanation_follows_the_ui_language() -> None:
    failures = [RuleFailure(RuleTag.VERB_TENSE, ())]

    ca = await generate(failures, count=1, language=UiLanguage.CA)
    es = await generate(failures, count=1, language=UiLanguage.ES)

    assert "passat simple" in ca.exercises[0].explanation
    assert "pasado simple" in es.exercises[0].explanation


async def test_generate_exercises_is_deterministic() -> None:
    failures = [RuleFailure(RuleTag.ARTICLES, ())]

    assert await generate(failures) == await generate(failures)
