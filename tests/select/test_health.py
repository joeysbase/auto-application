"""After a filter batch: usage limits, unjudgeable jobs, broken filters (docs/step4-run.md §2.3)."""

from datetime import datetime

from auto_app.select.engine import SelectReport, unjudgeable
from auto_app.select.health import assess, clear, kind_of, reset_time, unjudgeable_rows
from tests.select.test_review import env  # noqa: F401 — env is a fixture

NOW = datetime(2026, 10, 6, 13, 0)


def rep(failures, succeeded=None):
    r = SelectReport()
    r.failures, r.succeeded = failures, succeeded or {}
    return r


def test_kinds_and_reset_times():
    assert kind_of("Claude AI usage limit reached|1791241200") == "usage"
    assert kind_of("API error 429: rate_limit_error") == "usage" and kind_of("Insufficient Balance (402)") == "usage"
    assert kind_of("Invalid API key · Please run /login") == "broken" and kind_of("model: claude-x not found") == "broken"
    assert kind_of("timed out after 600 s") == "job" and kind_of("couldn't fetch the page") == "job"
    assert reset_time("5-hour limit reached ∙ resets 3pm", NOW, 900) == datetime(2026, 10, 6, 15, 0)
    assert reset_time("resets at 9:30am", NOW, 900) == datetime(2026, 10, 7, 9, 30)  # past today: tomorrow
    assert reset_time("rate limited", NOW, 900) == datetime(2026, 10, 6, 13, 15)  # none given: retry interval


def test_usage_never_counts_against_a_job(env):  # noqa: F811
    db = env[0]
    h = assess(rep([(1, "ml", "usage limit reached, resets 3pm"), (2, "ml", "usage limit reached, resets 3pm")]),
               NOW, store=db)
    assert h.usage_until == datetime(2026, 10, 6, 15, 0) and not h.broken and not h.unjudgeable
    assert unjudgeable_rows(db) == []


def test_a_broken_filter(env):  # noqa: F811
    db = env[0]
    h = assess(rep([(1, "ml", "Invalid API key · Please run /login")]), NOW, store=db)
    assert "Invalid API key" in h.broken["ml"]  # a login / key error: broken at once
    h = assess(rep([(1, "research", "timeout"), (2, "research", "timeout"), (3, "research", "timeout")]), NOW, store=db)
    assert "3 different jobs failed, none worked" in h.broken["research"]  # failing on everything: broken
    assert unjudgeable_rows(db) == []  # … and those jobs aren't blamed


def test_a_job_that_keeps_failing_becomes_unjudgeable(env):  # noqa: F811
    db, _, jobs, _ = env
    j = jobs["ml"]
    for n in range(3):
        h = assess(rep([(j, "ml", "page unreadable")], succeeded={"ml": 4}), NOW, tries_per_job=3, store=db)
    assert h.unjudgeable == [(j, "ml", "page unreadable")] and not h.broken
    assert (j, "ml") in unjudgeable(db)  # `filter run` skips it now
    clear(db, [j])  # `filter run --job N`: a fresh start
    assert (j, "ml") not in unjudgeable(db)
