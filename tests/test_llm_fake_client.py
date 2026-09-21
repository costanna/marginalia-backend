from app.db.models import Category, RuleTag, UiLanguage
from app.services.llm.base import LLMAnalysis
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
