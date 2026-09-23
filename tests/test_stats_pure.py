from datetime import date, timedelta

import pytest

from app.services.stats import compute_streak

TODAY = date(2026, 9, 22)


def days_ago(*n: int) -> set[date]:
    return {TODAY - timedelta(days=i) for i in n}


def test_no_activity_at_all_is_a_zero_streak() -> None:
    assert compute_streak(set(), TODAY) == 0


def test_activity_only_today_is_a_streak_of_one() -> None:
    assert compute_streak(days_ago(0), TODAY) == 1


def test_consecutive_days_up_to_today_are_counted() -> None:
    assert compute_streak(days_ago(0, 1, 2, 3), TODAY) == 4


def test_a_gap_stops_the_count() -> None:
    assert compute_streak(days_ago(0, 1, 3, 4), TODAY) == 2  # today and yesterday only


def test_nothing_today_but_yesterday_still_alive_keeps_the_streak() -> None:
    # "Today is not over yet": a missing day for today alone does not break what came before.
    assert compute_streak(days_ago(1, 2, 3), TODAY) == 3


def test_two_idle_days_in_a_row_end_the_streak() -> None:
    assert compute_streak(days_ago(2, 3, 4), TODAY) == 0  # neither today nor yesterday


def test_activity_older_than_the_streak_does_not_extend_it() -> None:
    # A day active 10 days ago, with a gap right before today, must not be added in.
    assert compute_streak(days_ago(0, 10), TODAY) == 1


@pytest.mark.parametrize("n", [1, 5, 30, 100])
def test_an_unbroken_run_of_n_days_counts_exactly_n(n: int) -> None:
    assert compute_streak(days_ago(*range(n)), TODAY) == n


def test_future_days_are_irrelevant(monkeypatch: pytest.MonkeyPatch) -> None:
    # Defensive: a clock skew or a bad "today" must not make the function misbehave.
    future_days = days_ago(-1, -2)  # "tomorrow" and "the day after", i.e. after TODAY
    assert compute_streak(future_days, TODAY) == 0
