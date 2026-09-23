"""Deterministic, offline LLM client for development and tests (no cost, no network).

It "corrects" a small catalogue of common learner mistakes with regular expressions. The output
has exactly the shape of a real model's answer, so it goes through the same validation.
"""

import re
from dataclasses import dataclass
from typing import Any

from app.db.models import Category, ExerciseType, RuleTag, TargetLevel, UiLanguage
from app.services.llm.base import RuleFailure


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
    fr: str,
) -> FakeRule:
    return FakeRule(
        re.compile(pattern),
        suggestion,
        category,
        rule_tag,
        {UiLanguage.CA: ca, UiLanguage.ES: es, UiLanguage.EN: en, UiLanguage.FR: fr},
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
        "Avec 'yesterday', on utilise le passé simple : 'went'.",
    ),
    _rule(
        r"\bgoed\b",
        "went",
        Category.GRAMMAR,
        RuleTag.VERB_TENSE,
        "'Go' és irregular: el passat és 'went'.",
        "'Go' es irregular: su pasado es 'went'.",
        "'Go' is irregular: its past is 'went'.",
        "'Go' est irrégulier : son passé est 'went'.",
    ),
    _rule(
        r"\bI has\b",
        "I have",
        Category.GRAMMAR,
        RuleTag.SUBJECT_VERB_AGREEMENT,
        "Amb 'I' s'usa 'have', no 'has'.",
        "Con 'I' se usa 'have', no 'has'.",
        "With 'I' use 'have', not 'has'.",
        "Avec 'I', on utilise 'have', pas 'has'.",
    ),
    _rule(
        r"\ba(?= [aeiouAEIOU])",
        "an",
        Category.GRAMMAR,
        RuleTag.ARTICLES,
        "Davant d'un so vocàlic s'usa 'an'.",
        "Ante un sonido vocálico se usa 'an'.",
        "Before a vowel sound use 'an'.",
        "Devant un son vocalique, on utilise 'an'.",
    ),
    _rule(
        r"\brecieve\b",
        "receive",
        Category.SPELLING,
        RuleTag.SPELLING_COMMON,
        "S'escriu 'receive': després de 'c', primer la 'e'.",
        "Se escribe 'receive': después de 'c', primero la 'e'.",
        "It is spelt 'receive': after 'c', 'e' comes first.",
        "Ça s'écrit 'receive' : après 'c', le 'e' vient en premier.",
    ),
    _rule(
        r"\bteh\b",
        "the",
        Category.SPELLING,
        RuleTag.SPELLING_COMMON,
        "Falta d'ortografia: 'the'.",
        "Error de escritura: 'the'.",
        "Typo: 'the'.",
        "Faute de frappe : 'the'.",
    ),
    _rule(
        r"\bdont\b",
        "don't",
        Category.PUNCTUATION,
        RuleTag.OTHER,
        "Falta l'apòstrof: 'don't'.",
        "Falta el apóstrofo: 'don't'.",
        "The apostrophe is missing: 'don't'.",
        "L'apostrophe manque : 'don't'.",
    ),
)

SUMMARIES: dict[UiLanguage, str] = {
    UiLanguage.CA: "Bon començament! He trobat {n} coses per millorar; ho tens a prop.",
    UiLanguage.ES: "¡Buen comienzo! He encontrado {n} cosas que mejorar; lo tienes cerca.",
    UiLanguage.EN: "Good start! I found {n} things to improve; you are close.",
    UiLanguage.FR: "Bon début ! J'ai trouvé {n} choses à améliorer ; vous y êtes presque.",
}


def _estimate_level(word_count: int) -> str:
    """Crude length-based stand-in for a real CEFR estimate (only meaningful for the fake)."""
    for limit, level in ((15, "A1"), (40, "A2"), (100, "B1"), (200, "B2")):
        if word_count < limit:
            return level
    return "C1"


@dataclass(frozen=True)
class FakeExerciseTemplate:
    fill_blank_prompt: str
    fill_blank_answer: str
    multiple_choice_prompt: str
    multiple_choice_options: tuple[str, ...]
    multiple_choice_answer: str
    explanation: dict[UiLanguage, str]


def _exercise(
    fill_blank_prompt: str,
    fill_blank_answer: str,
    multiple_choice_prompt: str,
    multiple_choice_options: tuple[str, ...],
    multiple_choice_answer: str,
    ca: str,
    es: str,
    en: str,
    fr: str,
) -> FakeExerciseTemplate:
    return FakeExerciseTemplate(
        fill_blank_prompt,
        fill_blank_answer,
        multiple_choice_prompt,
        multiple_choice_options,
        multiple_choice_answer,
        {UiLanguage.CA: ca, UiLanguage.ES: es, UiLanguage.EN: en, UiLanguage.FR: fr},
    )


# One canned exercise (of each type) per rule the fake client knows how to teach. A real provider
# writes fresh sentences from the learner's own mistakes; the fake only needs to be deterministic
# and speak the same contract, so a small, hand-picked catalogue is enough.
EXERCISE_TEMPLATES: dict[RuleTag, FakeExerciseTemplate] = {
    RuleTag.VERB_TENSE: _exercise(
        "Yesterday I ___ to the cinema with my friends.",
        "went",
        "Yesterday I ___ to the cinema with my friends.",
        ("go", "went", "goes", "gone"),
        "went",
        "Amb 'yesterday' s'usa el passat simple: 'went'.",
        "Con 'yesterday' se usa el pasado simple: 'went'.",
        "With 'yesterday' use the past simple: 'went'.",
        "Avec 'yesterday', on utilise le passé simple : 'went'.",
    ),
    RuleTag.SUBJECT_VERB_AGREEMENT: _exercise(
        "I ___ a new bike.",
        "have",
        "I ___ a new bike.",
        ("has", "have", "having", "haves"),
        "have",
        "Amb 'I' s'usa 'have', no 'has'.",
        "Con 'I' se usa 'have', no 'has'.",
        "With 'I' use 'have', not 'has'.",
        "Avec 'I', on utilise 'have', pas 'has'.",
    ),
    RuleTag.ARTICLES: _exercise(
        "She is ___ engineer.",
        "an",
        "She is ___ engineer.",
        ("a", "an", "the", "-"),
        "an",
        "Davant d'un so vocàlic s'usa 'an'.",
        "Ante un sonido vocálico se usa 'an'.",
        "Before a vowel sound use 'an'.",
        "Devant un son vocalique, on utilise 'an'.",
    ),
    RuleTag.SPELLING_COMMON: _exercise(
        "Please open ___ door.",
        "the",
        "Please open ___ door.",
        ("teh", "the", "hte", "th"),
        "the",
        "S'escriu 'the'.",
        "Se escribe 'the'.",
        "It is spelt 'the'.",
        "Ça s'écrit 'the'.",
    ),
}

# Used for any rule outside the small catalogue above (still a real, closed RuleTag value).
_GENERIC_EXERCISE_TEMPLATE = _exercise(
    "She ___ like the cinema.",
    "doesn't",
    "She ___ like the cinema.",
    ("dont", "don't", "do'nt", "doesnt"),
    "don't",
    "Falta l'apòstrof: 'don't'.",
    "Falta el apóstrofo: 'don't'.",
    "The apostrophe is missing: 'don't'.",
    "L'apostrophe manque : 'don't'.",
)


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

    async def generate_exercises(
        self,
        *,
        rule_failures: list[RuleFailure],
        ui_language: UiLanguage,
        count: int,
    ) -> dict[str, Any]:
        # Deterministic: cycles through the given rules (falling back to `other` when there are
        # none) and alternates the two exercise types, so tests can assert exact output.
        rule_tags = [failure.rule_tag for failure in rule_failures] or [RuleTag.OTHER]
        exercises: list[dict[str, Any]] = []
        for index in range(count):
            rule_tag = rule_tags[index % len(rule_tags)]
            template = EXERCISE_TEMPLATES.get(rule_tag, _GENERIC_EXERCISE_TEMPLATE)
            explanation = template.explanation[ui_language]
            if index % 2 == 0:
                exercises.append(
                    {
                        "rule_tag": rule_tag.value,
                        "type": ExerciseType.FILL_BLANK.value,
                        "prompt": template.fill_blank_prompt,
                        "options": None,
                        "correct_answer": template.fill_blank_answer,
                        "explanation": explanation,
                    }
                )
            else:
                exercises.append(
                    {
                        "rule_tag": rule_tag.value,
                        "type": ExerciseType.MULTIPLE_CHOICE.value,
                        "prompt": template.multiple_choice_prompt,
                        "options": list(template.multiple_choice_options),
                        "correct_answer": template.multiple_choice_answer,
                        "explanation": explanation,
                    }
                )
        return {"exercises": exercises}
