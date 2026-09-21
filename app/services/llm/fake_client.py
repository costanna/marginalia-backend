"""Deterministic, offline LLM client for development and tests (no cost, no network).

It "corrects" a small catalogue of common learner mistakes with regular expressions. The output
has exactly the shape of a real model's answer, so it goes through the same validation.
"""

import re
from dataclasses import dataclass
from typing import Any

from app.db.models import Category, RuleTag, TargetLevel, UiLanguage


@dataclass(frozen=True)
class FakeRule:
    pattern: re.Pattern[str]  # the whole match is the incorrect fragment
    suggestion: str
    category: Category
    rule_tag: RuleTag
    explanation: dict[UiLanguage, str]


def _rule(
    pattern: str,
    suggestion: str,
    category: Category,
    rule_tag: RuleTag,
    ca: str,
    es: str,
    en: str,
) -> FakeRule:
    return FakeRule(
        re.compile(pattern),
        suggestion,
        category,
        rule_tag,
        {UiLanguage.CA: ca, UiLanguage.ES: es, UiLanguage.EN: en},
    )


FAKE_RULES: tuple[FakeRule, ...] = (
    _rule(
        r"(?<=[Yy]esterday I )go\b",
        "went",
        Category.GRAMMAR,
        RuleTag.VERB_TENSE,
        "Amb 'yesterday' s'usa el passat simple: 'went'.",
        "Con 'yesterday' se usa el pasado simple: 'went'.",
        "With 'yesterday' use the past simple: 'went'.",
    ),
    _rule(
        r"\bgoed\b",
        "went",
        Category.GRAMMAR,
        RuleTag.VERB_TENSE,
        "'Go' és irregular: el passat és 'went'.",
        "'Go' es irregular: su pasado es 'went'.",
        "'Go' is irregular: its past is 'went'.",
    ),
    _rule(
        r"\bI has\b",
        "I have",
        Category.GRAMMAR,
        RuleTag.SUBJECT_VERB_AGREEMENT,
        "Amb 'I' s'usa 'have', no 'has'.",
        "Con 'I' se usa 'have', no 'has'.",
        "With 'I' use 'have', not 'has'.",
    ),
    _rule(
        r"\ba(?= [aeiouAEIOU])",
        "an",
        Category.GRAMMAR,
        RuleTag.ARTICLES,
        "Davant d'un so vocàlic s'usa 'an'.",
        "Ante un sonido vocálico se usa 'an'.",
        "Before a vowel sound use 'an'.",
    ),
    _rule(
        r"\brecieve\b",
        "receive",
        Category.SPELLING,
        RuleTag.SPELLING_COMMON,
        "S'escriu 'receive': després de 'c', primer la 'e'.",
        "Se escribe 'receive': después de 'c', primero la 'e'.",
        "It is spelt 'receive': after 'c', 'e' comes first.",
    ),
    _rule(
        r"\bteh\b",
        "the",
        Category.SPELLING,
        RuleTag.SPELLING_COMMON,
        "Falta d'ortografia: 'the'.",
        "Error de escritura: 'the'.",
        "Typo: 'the'.",
    ),
    _rule(
        r"\bdont\b",
        "don't",
        Category.PUNCTUATION,
        RuleTag.OTHER,
        "Falta l'apòstrof: 'don't'.",
        "Falta el apóstrofo: 'don't'.",
        "The apostrophe is missing: 'don't'.",
    ),
)

SUMMARIES: dict[UiLanguage, str] = {
    UiLanguage.CA: "Bon començament! He trobat {n} coses per millorar; ho tens a prop.",
    UiLanguage.ES: "¡Buen comienzo! He encontrado {n} cosas que mejorar; lo tienes cerca.",
    UiLanguage.EN: "Good start! I found {n} things to improve; you are close.",
}


def _estimate_level(word_count: int) -> str:
    """Crude length-based stand-in for a real CEFR estimate (only meaningful for the fake)."""
    for limit, level in ((15, "A1"), (40, "A2"), (100, "B1"), (200, "B2")):
        if word_count < limit:
            return level
    return "C1"


class FakeLLMClient:
    model_name = "fake-llm"

    async def analyze_text(
        self,
        *,
        text: str,
        ui_language: UiLanguage,
        target_level: TargetLevel | None,
    ) -> dict[str, Any]:
        found = [
            (match.start(), rule, match.group())
            for rule in FAKE_RULES
            for match in rule.pattern.finditer(text)
        ]
        corrections = [
            {
                "original": fragment,
                "suggestion": rule.suggestion,
                "category": rule.category.value,
                "rule_tag": rule.rule_tag.value,
                "explanation": rule.explanation[ui_language],
            }
            for _, rule, fragment in sorted(found, key=lambda item: item[0])
        ]
        return {
            "cefr_level": _estimate_level(len(text.split())),
            "summary": SUMMARIES[ui_language].format(n=len(corrections)),
            "corrections": corrections,
        }
