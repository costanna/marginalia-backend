from typing import Any

import pytest

from app.core.errors import AppError
from app.db.models import TargetLevel, UiLanguage
from app.services.analysis import analyze, ask_llm
from app.services.llm.base import LLMInvalidResponseError, LLMUnavailableError
from app.services.llm.fake_client import FakeLLMClient

VALID: dict[str, Any] = {
    "cefr_level": "A2",
    "summary": "Good.",
    "corrections": [
        {
            "original": "go",
            "suggestion": "went",
            "category": "grammar",
            "rule_tag": "verb_tense",
            "explanation": "Past.",
        }
    ],
}
TEXT = "Yesterday I go to the cinema with my friends."


class ScriptedClient:
    """Returns (or raises) the scripted answers in order and counts the calls."""

    model_name = "scripted"

    def __init__(self, *answers: dict[str, Any] | Exception) -> None:
        self.answers = list(answers)
        self.calls = 0

    async def analyze_text(self, **_: object) -> dict[str, Any]:
        self.calls += 1
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


async def run_ask(client: ScriptedClient) -> Any:
    return await ask_llm(client, text=TEXT, ui_language=UiLanguage.EN, target_level=None)


async def test_a_valid_answer_needs_a_single_call() -> None:
    client = ScriptedClient(VALID)

    result = await run_ask(client)

    assert result.cefr_level == "A2"
    assert client.calls == 1


@pytest.mark.parametrize(
    "bad_first_answer",
    [
        LLMInvalidResponseError("not json"),
        {"cefr_level": "Z9", "summary": "x", "corrections": []},  # fails Pydantic validation
        {"summary": "missing the level"},
    ],
    ids=["provider-said-invalid", "bad-enum", "missing-field"],
)
async def test_an_unusable_answer_is_retried_once(bad_first_answer: Any) -> None:
    client = ScriptedClient(bad_first_answer, VALID)

    result = await run_ask(client)

    assert result.cefr_level == "A2"
    assert client.calls == 2


async def test_two_unusable_answers_end_in_llm_invalid_response() -> None:
    client = ScriptedClient(LLMInvalidResponseError("x"), {"nope": 1}, VALID)

    with pytest.raises(AppError) as error:
        await run_ask(client)

    assert error.value.code == "llm_invalid_response"
    assert error.value.status_code == 502
    assert client.calls == 2  # never a third attempt


async def test_an_unavailable_provider_is_not_retried() -> None:
    client = ScriptedClient(LLMUnavailableError(), VALID)

    with pytest.raises(AppError) as error:
        await run_ask(client)

    assert error.value.code == "llm_unavailable"
    assert error.value.status_code == 503
    assert client.calls == 1


async def test_analyze_produces_the_documented_result() -> None:
    result = await analyze(
        FakeLLMClient(), text=TEXT, ui_language=UiLanguage.ES, target_level=None, max_chars=3000
    )

    assert result.original_text == TEXT
    assert result.corrected_text == "Yesterday I went to the cinema with my friends."
    assert result.word_count == 9
    assert [(c.start, c.end, c.original, c.suggestion) for c in result.corrections] == [
        (12, 14, "go", "went")
    ]
    assert "pasado simple" in result.corrections[0].explanation


async def test_analyze_cleans_the_text_and_offsets_refer_to_the_cleaned_version() -> None:
    messy = "  Yesterday I go to the\r\ncinema.\x00  "

    result = await analyze(
        FakeLLMClient(), text=messy, ui_language=UiLanguage.EN, target_level=None, max_chars=3000
    )

    assert result.original_text == "Yesterday I go to the\ncinema."
    [item] = result.corrections
    assert result.original_text[item.start : item.end] == "go"


async def test_analyze_rejects_a_bad_length_before_calling_the_llm() -> None:
    client = ScriptedClient(VALID)

    with pytest.raises(AppError) as error:
        await analyze(
            client, text="too short", ui_language=UiLanguage.EN, target_level=None, max_chars=3000
        )

    assert error.value.code == "text_too_short"
    assert client.calls == 0


async def test_corrections_the_model_invented_are_silently_dropped() -> None:
    answer = {
        **VALID,
        "corrections": [
            VALID["corrections"][0],
            {**VALID["corrections"][0], "original": "not in the text at all"},
        ],
    }

    result = await analyze(
        ScriptedClient(answer),
        text=TEXT,
        ui_language=UiLanguage.EN,
        target_level=TargetLevel.B1,
        max_chars=3000,
    )

    assert [c.original for c in result.corrections] == ["go"]
