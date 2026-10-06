"""A collect round's goal (docs/step4-run.md §2.1): time spread across searches, volume caps, caught up."""

from collections import Counter

from auto_app.collect.goal import Budget, RoundGoal, caught_up
from auto_app.core.models import StopReason


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def test_time_is_spread_across_searches_and_leftover_flows_on():
    clock = Clock()
    b = RoundGoal(time_limit_s=300).budget(clock)
    b.start_query(3)
    assert b.query_deadline == 100  # 300 s over 3 searches
    clock.t = 20  # the first search was caught up quickly
    b.start_query(2)
    assert b.query_deadline == 20 + 140  # the 80 s it didn't need go to the others
    clock.t = 161
    assert b.query_over() == StopReason.TIME_LIMIT and b.exhausted() is None  # this search's share, not the round
    clock.t = 300
    assert b.exhausted() == StopReason.TIME_LIMIT and b.hit == StopReason.TIME_LIMIT


def test_volume_caps():
    b = Budget(RoundGoal(max_new_postings=2, max_pages=10))
    b.new_postings = 2
    assert b.exhausted() == StopReason.GOAL
    b = Budget(RoundGoal(max_pages=3))
    b.pages = 3
    assert b.exhausted() == StopReason.GOAL
    assert Budget(RoundGoal()).exhausted() is None and not RoundGoal().any


def test_caught_up_means_every_search_saw_everything_new():
    assert caught_up(Counter({"early_stop": 2, "done": 1}))
    assert not caught_up(Counter({"early_stop": 2, "time_limit": 1}))  # one search ran out of time
    assert not caught_up(Counter({"max_pages": 1}))
    assert not caught_up(Counter({"done": 1}), skipped=1)  # a search never started
    assert not caught_up(Counter())


def test_durations():
    from auto_app.core.duration import format_duration, parse_duration
    import pytest

    assert parse_duration("30m") == 1800 and parse_duration("1h30m") == 5400 and parse_duration("90s") == 90
    assert parse_duration("45") == 2700 and parse_duration(2) == 120  # a bare number: minutes
    with pytest.raises(ValueError):
        parse_duration("soon")
    assert format_duration(1800) == "30 min" and format_duration(5400) == "1h30m" and format_duration(42) == "42 s"


def test_a_search_out_of_its_time_share_names_the_cap():
    from collections import Counter as C

    from auto_app.collect.collector_base import CollectorBase

    c = CollectorBase.__new__(CollectorBase)
    c.budget, c.stop_reasons = Budget(RoundGoal(time_limit_s=120)), C({"done": 1, "time_limit": 1})
    assert c._goal_hit() == "time_limit"  # the round's budget itself wasn't used up, but a search's share was
