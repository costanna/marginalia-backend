import pytest

from app.core.errors import AppError
from app.db.models import Category, RuleTag
from app.services.analysis import (
    MIN_TEXT_CHARS,
    LocatedCorrection,
    apply_corrections,
    clean_text,
    count_words,
    locate_corrections,
    validate_length,
)
from app.services.llm.base import LLMCorrection


def fix(original: str, suggestion: str) -> LLMCorrection:
    return LLMCorrection(
        original=original,
        suggestion=suggestion,
        category=Category.GRAMMAR,
        rule_tag=RuleTag.OTHER,
        explanation="because",
    )


def located(text: str, *fixes: LLMCorrection) -> list[LocatedCorrection]:
    return locate_corrections(text, list(fixes))


# --- clean_text ---------------------------------------------------------------------------------


def test_clean_text_normalises_line_breaks_and_trims() -> None:
    assert clean_text("  first\r\nsecond\rthird  \n") == "first\nsecond\nthird"


def test_clean_text_removes_control_characters_but_keeps_newlines_and_tabs() -> None:
    assert clean_text("a\x00b\x07c\td\ne") == "abc\td\ne"


def test_clean_text_composes_accents() -> None:
    decomposed = "café"  # "e" followed by a combining acute accent: 5 code points
    composed = "café"  # a single "é": 4 code points

    # Guards against an editor silently normalising the literals above.
    assert (len(decomposed), len(composed)) == (5, 4)
    assert clean_text(decomposed) == composed


def test_clean_text_keeps_emoji_and_non_latin_text() -> None:
    assert clean_text("Hi 😀 你好") == "Hi 😀 你好"


# --- count_words --------------------------------------------------------------------------------


def test_count_words_matches_the_spec_example() -> None:
    assert count_words("Yesterday I go to the cinema with my friends.") == 9


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("", 0),
        ("...", 0),
        ("don't stop", 2),
        ("a well-known author", 3),
        ("it’s fine", 2),  # typographic apostrophe
        ("café olé", 2),
        ("one\ntwo\tthree", 3),
    ],
)
def test_count_words(text: str, expected: int) -> None:
    assert count_words(text) == expected


# --- validate_length ----------------------------------------------------------------------------


def test_validate_length_accepts_the_boundaries() -> None:
    validate_length("x" * MIN_TEXT_CHARS, 3000)
    validate_length("x" * 3000, 3000)


@pytest.mark.parametrize(
    ("length", "code"),
    [(MIN_TEXT_CHARS - 1, "text_too_short"), (3001, "text_too_long")],
)
def test_validate_length_rejects_out_of_range_texts(length: int, code: str) -> None:
    with pytest.raises(AppError) as error:
        validate_length("x" * length, 3000)

    assert error.value.code == code
    assert error.value.status_code == 422
    assert error.value.details == {"min": MIN_TEXT_CHARS, "max": 3000, "length": length}


# --- locate_corrections -------------------------------------------------------------------------


def test_offsets_point_at_the_original_fragment() -> None:
    text = "Yesterday I go to the cinema."

    [item] = located(text, fix("go", "went"))

    assert (item.start, item.end) == (12, 14)
    assert text[item.start : item.end] == "go"


def test_a_fragment_is_only_matched_as_a_whole_word() -> None:
    text = "I google it, I go there, a long time ago."

    [item] = located(text, fix("go", "went"))

    assert text[item.start - 2 : item.end + 6] == "I go there"


def test_punctuation_fragments_are_not_subject_to_word_boundaries() -> None:
    text = "Hello ,world"

    [item] = located(text, fix(" ,", ", "))

    assert text[item.start : item.end] == " ,"


def test_a_fragment_missing_from_the_text_is_dropped() -> None:
    assert located("I went home.", fix("goed", "went")) == []


def test_matching_is_case_sensitive_and_literal() -> None:
    assert located("I Go home.", fix("go", "went")) == []


def test_a_correction_that_changes_nothing_is_dropped() -> None:
    assert located("I went home.", fix("went", "went")) == []


def test_repeated_fragments_take_successive_occurrences() -> None:
    text = "I go there and I go back."

    items = located(text, fix("go", "went"), fix("go", "went"))

    assert [item.start for item in items] == [text.index("go"), text.rindex("go")]


def test_a_third_entry_for_a_fragment_that_appears_twice_is_dropped() -> None:
    items = located("I go and I go.", fix("go", "went"), fix("go", "went"), fix("go", "went"))

    assert len(items) == 2


def test_overlapping_corrections_keep_the_first_one_only() -> None:
    text = "She don't likes it."

    items = located(text, fix("don't likes", "doesn't like"), fix("likes", "like"))

    assert [(item.original, item.suggestion) for item in items] == [("don't likes", "doesn't like")]


def test_adjacent_corrections_are_not_overlaps() -> None:
    text = "I has a apple."

    items = located(text, fix("has", "have"), fix("a", "an"))

    assert [item.original for item in items] == ["has", "a"]


def test_results_are_sorted_by_position_whatever_the_model_order() -> None:
    text = "I recieve teh letter."

    items = located(text, fix("teh", "the"), fix("recieve", "receive"))

    assert [item.original for item in items] == ["recieve", "teh"]


def test_offsets_are_code_points_so_emoji_do_not_shift_them() -> None:
    text = "😀😀 Yesterday I go home"  # each emoji is ONE code point (two in UTF-16)

    [item] = located(text, fix("go", "went"))

    assert text[item.start : item.end] == "go"
    assert item.start == len("😀😀 Yesterday I ")


def test_accented_text_is_located_correctly() -> None:
    text = "Él dice que yo go a la escuela"

    [item] = located(text, fix("go", "went"))

    assert text[item.start : item.end] == "go"


def test_decomposed_input_matches_after_cleaning() -> None:
    # The learner's keyboard produced "e" + accent; the model answered with the composed "é".
    raw = "I like café a lot, really."
    composed = "café"

    assert located(raw, fix(composed, "coffee")) == []  # without cleaning the match fails...
    assert located(clean_text(raw), fix(composed, "coffee"))  # ...and NFC makes it work


def test_offsets_always_slice_back_to_the_original_fragment() -> None:
    text = "I has a apple. She go home. We dont like teh rain."
    fixes = [fix("I has", "I have"), fix("a", "an"), fix("go", "goes"), fix("teh", "the")]

    for item in located(text, *fixes):
        assert text[item.start : item.end] == item.original


# --- apply_corrections --------------------------------------------------------------------------


def test_apply_replaces_from_the_end_so_lengths_do_not_shift_positions() -> None:
    text = "I has a apple and teh book."

    items = located(text, fix("I has", "I have"), fix("a", "an"), fix("teh", "the"))

    assert apply_corrections(text, items) == "I have an apple and the book."


def test_apply_handles_replacements_that_shrink_and_grow_the_text() -> None:
    text = "It was a very very good day."

    items = located(text, fix("very very good", "excellent"), fix("It", "This day"))

    assert apply_corrections(text, items) == "This day was a excellent day."


def test_apply_can_delete_a_fragment() -> None:
    text = "I went to to the shop."

    items = located(text, fix("to to", "to"))

    assert apply_corrections(text, items) == "I went to the shop."


def test_apply_with_unicode() -> None:
    text = "😀 Yesterday I go to café, I has time."

    items = located(text, fix("go", "went"), fix("I has", "I have"))

    assert apply_corrections(text, items) == "😀 Yesterday I went to café, I have time."


def test_apply_without_corrections_returns_the_text_unchanged() -> None:
    assert apply_corrections("Nothing to fix here.", []) == "Nothing to fix here."
