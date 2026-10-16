"""`auto-app serve` (docs/step4-run.md §1–§3): the loop, with a fake clock, fake collect and fake judge."""

from datetime import datetime, timedelta

import pytest
from typer.testing import CliRunner

from auto_app.select.engine import SelectReport
from auto_app.serve import BrokenFilters, Serve, ServeConfig, State, Window, describe, format_round, goal_for

START = datetime(2026, 10, 6, 9, 0)  # a Tuesday morning, inside the default window


class Clock:
    def __init__(self, t=START):
        self.t = t

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += timedelta(seconds=s)


def batch(new=3, caught_up=True):
    return {"sources": {"linkedin": {"status": "ok", "outcomes": {"fetched": new}, "pages_loaded": 4,
                                     "caught_up": caught_up, "goal_hit": None if caught_up else "time_limit"}}}


def report(judged=3, recommended=1, failures=(), succeeded=None):
    r = SelectReport()
    r.judged, r.recommended_jobs, r.failures = judged, recommended, list(failures)
    r.succeeded = succeeded if succeeded is not None else {"ml": judged - len(failures)}
    return r


@pytest.fixture
def make(tmp_path):
    def make(judge=None, collect=None, cfg=None, store=None, retention=None):
        clock = Clock()
        told = []
        calls = {"collect": 0, "judge": 0}

        def do_collect(sources, goals):
            calls["collect"] += 1
            clock.sleep(600)  # collecting takes 10 min
            return (collect or (lambda s, g: (batch(), 3)))(sources, goals)

        def do_judge():
            calls["judge"] += 1
            clock.sleep(300)  # judging takes 5 min
            return (judge or (lambda n: report()))(calls["judge"])
        s = Serve(cfg or ServeConfig(jitter=0), State(tmp_path / "serve.json"), do_collect, do_judge,
                  lambda title, text: told.append(text), store=store, rounds_log=tmp_path / "rounds.jsonl",
                  clock=clock, sleep=clock.sleep, tick_s=60, rand=lambda: 0.5, retention=retention)
        s.told, s.calls, s.clock_ = told, calls, clock
        return s
    return make


def test_a_round_then_the_pause_from_the_end_of_collecting(make, tmp_path):
    s = make()
    r = s.step(["linkedin"])
    assert r.collect_ended == START + timedelta(minutes=10) and r.filtered_at == START + timedelta(minutes=15)
    assert r.next_round_at == START + timedelta(minutes=40)  # 30 min pause counted from collecting's end
    assert s.blocked(s.clock_()) == "pausing until 09:40"
    s.clock_.t = START + timedelta(minutes=40)
    assert s.blocked(s.clock_()) is None
    st = s.state.read()
    assert st["last_round"]["sources"]["linkedin"] == {"new": 3, "pages": 4, "caught_up": True, "goal_hit": None,
                                                       "status": "ok", "stopped_by": None}
    assert "1 new recommendation(s) — ask Claude Code" in s.told[0]
    assert (tmp_path / "rounds.jsonl").read_text().count("\n") == 1
    text = format_round(st["last_round"])
    assert "linkedin: caught up, 3 new / 4 pages" in text and "next round ≈ 09:40" in text


def test_a_slow_batch_holds_the_next_round(make):
    s = make(judge=lambda n: report(judged=40, recommended=0))
    s.state.update(filter_pending=True, next_round_at=(START - timedelta(minutes=1)).isoformat())
    assert s.blocked(START) == "the last round's jobs are still being judged"  # the pause is over, the batch isn't


def test_window_and_pause(make):
    s = make(cfg=ServeConfig(window=Window(days="mon-fri", start="08:00", end="18:00"), jitter=0))
    assert s.blocked(datetime(2026, 10, 10, 10, 0)).startswith("outside the window")  # Saturday
    assert s.blocked(datetime(2026, 10, 6, 19, 0)).startswith("outside the window")
    s.state.update(paused_until=(START + timedelta(hours=2)).isoformat())
    assert s.blocked(START).startswith("paused until")
    assert s.blocked(START + timedelta(hours=3)) is None
    w = Window(days="sat,sun")
    assert w.day_set() == {5, 6} and Window(days="fri-mon").day_set() == {4, 5, 6, 0}


def test_no_usage_left_waits_then_judges_again(make):
    s = make(judge=lambda n: report(judged=2, failures=[(1, "ml", "usage limit reached ∙ resets 11am")]) if n == 1
             else report())
    r = s.step(["linkedin"])
    assert s.calls["judge"] == 2  # after the reset, the batch ran again
    assert s.clock_() >= datetime(2026, 10, 6, 11, 0) and r.filtered_at >= datetime(2026, 10, 6, 11, 0)
    assert any("usage" in t for t in s.told) and not s.state.read().get("stopped")
    assert not s.state.read().get("usage_until")


def test_a_broken_filter_stops_collecting_until_you_resume(make, tmp_path):
    s = make(judge=lambda n: report(judged=1, failures=[(1, "ml", "Invalid API key · Please run /login")],
                                    succeeded={}))
    r = s.step(["linkedin"])
    st = s.state.read()
    assert "filter ml is broken" in st["stopped"] and st["status"] == "stopped"
    assert any("broken" in t for t in s.told)  # you're told at once
    s.clock_.t += timedelta(hours=5)
    assert s.blocked(s.clock_()).startswith("stopped:")  # no new round, however long
    assert r.problems and "Invalid API key" in r.problems[0]

    from auto_app.cli import app

    import os
    os.environ["AUTO_APP_HOME"] = str(tmp_path)  # serve resume / status read <home>/data/serve.json
    (tmp_path / "data").mkdir(exist_ok=True)
    (tmp_path / "data" / "serve.json").write_text((tmp_path / "serve.json").read_text())
    try:
        out = CliRunner().invoke(app, ["serve", "status"]).output
        assert "stopped" in out and "serve resume" in out
        out = CliRunner().invoke(app, ["serve", "resume"]).output
        assert "the backlog is judged first" in out
        st = State(tmp_path / "data" / "serve.json").read()
        assert "stopped" not in st and st["filter_pending"] is True
    finally:
        del os.environ["AUTO_APP_HOME"]


def test_a_filter_that_wont_load_stops_too(make):
    def judge(n):
        raise BrokenFilters({"ml": "doesn't load: filter.yaml: unknown field"})
    s = make(judge=judge)
    s.step(["linkedin"])
    assert "filter ml: doesn't load" in s.state.read()["stopped"]


def test_a_job_that_keeps_failing_becomes_unjudgeable(make, tmp_path):
    from auto_app.core.db.store import DbStore
    from auto_app.settings import Paths

    db = DbStore(tmp_path / "data" / "auto_app.db", Paths(home=tmp_path))
    s = make(judge=lambda n: report(judged=4, failures=[(7, "ml", "page unreadable")], succeeded={"ml": 3}), store=db)
    r = s.step(["linkedin"])
    assert r.unjudgeable == 1 and not s.state.read().get("stopped")  # tried 3 times in the batch, then given up
    assert s.calls["judge"] == 3


def test_per_source_cycles_and_goals(make):
    from auto_app.serve import SourceCfg

    cfg = ServeConfig(sources={"companies": SourceCfg(every="6h"), "indeed": SourceCfg(enabled=False),
                               "linkedin": SourceCfg(time_limit="20m")}, jitter=0)
    s = make(cfg=cfg)
    assert s.due_sources(START, ["linkedin", "companies", "indeed"]) == ["linkedin", "companies"]
    s.state.update(source_rounds={"companies": (START - timedelta(hours=2)).isoformat()})
    assert s.due_sources(START, ["linkedin", "companies"]) == ["linkedin"]  # companies: 6 h not up yet
    assert goal_for(cfg, "linkedin").time_limit_s == 1200 and goal_for(cfg, "companies").time_limit_s == 1800
    assert "window mon-sun 08:00–23:00" in describe(cfg, {})


def test_once_says_why_no_round_starts_now(make):
    from auto_app.serve import NotNow

    s = make()
    s.state.update(paused=True)
    with pytest.raises(NotNow, match="paused"):
        s.run_forever(["linkedin"], once=True)
    assert s.calls["collect"] == 0


def test_resume_while_the_loop_waits_judges_the_backlog(make):
    class Collected(BaseException):  # not an Exception: a round catches those and the loop goes on
        pass

    def collect(sources, goals):
        raise Collected  # the round after the backlog: far enough for this test
    s = make(collect=collect)
    s.state.update(stopped="filter ml is broken: Invalid API key")
    sleep = s.sleep

    def resume_on_first_tick(sec):
        sleep(sec)
        if s.state.read().get("stopped"):
            s.state.update(stopped=None, filter_pending=True)  # what `serve resume` writes
    s.sleep = resume_on_first_tick
    with pytest.raises(Collected):
        s.run_forever(["linkedin"])
    assert s.calls["judge"] == 1 and s.calls["collect"] == 1  # the backlog was judged, then the next round began
    assert not s.state.read().get("filter_pending")


def test_a_paused_backlog_waits(make):
    from auto_app.serve import NotNow

    s = make()
    s.state.update(filter_pending=True, paused=True)
    with pytest.raises(NotNow, match="paused"):
        s.run_forever(["linkedin"], once=True)
    assert s.calls["judge"] == 0


def test_the_service_finds_uv_and_claude(monkeypatch):
    from auto_app import schedule, serve

    found = {"uv": "/opt/homebrew/bin/uv", "claude": "/Users/me/.local/bin/claude"}
    monkeypatch.setattr(schedule.shutil, "which", found.get)
    monkeypatch.setattr(schedule.Path, "home", lambda: schedule.Path("/Users/me"))
    path = serve.build_plist(uv="/opt/homebrew/bin/uv")["EnvironmentVariables"]["PATH"].split(":")
    assert path[:2] == ["/opt/homebrew/bin", "/Users/me/.local/bin"] and "/usr/bin" in path
    assert len(path) == len(set(path))
    assert schedule.build_plist(["09:00"], headless=True)["EnvironmentVariables"]["PATH"] == ":".join(path)


def test_the_template_is_the_defaults():
    from auto_app.serve import load_serve_config
    from auto_app.settings import PROJECT_ROOT

    assert load_serve_config(PROJECT_ROOT / "config" / "serve.example.yaml") == ServeConfig()


def test_the_report_names_what_stopped_a_source():
    r = {"started": "2026-10-06T14:26:00", "collect_ended": "2026-10-06T14:34:00",
         "sources": {"linkedin": {"new": 21, "pages": 24, "caught_up": False, "goal_hit": None, "status": "ok",
                                  "stopped_by": "max_pages"}}}
    assert "linkedin: not caught up (max_pages), 21 new / 24 pages" in format_round(r)  # its own page limit, not "ok"


def test_retention_runs_once_a_day(make):
    ran = []
    s = make(retention=lambda: ran.append(1) or "3 postings' raw data dropped")
    r = s.step(["linkedin"])
    assert ran == [1] and r.retention == "3 postings' raw data dropped"
    assert "retention: 3 postings' raw data dropped" in format_round(s.state.read()["last_round"])
    s.clock_.t = r.next_round_at
    assert s.step(["linkedin"]).retention is None and ran == [1]  # 40 min later: not again
    s.clock_.t += timedelta(hours=24)
    s.step(["linkedin"])
    assert ran == [1, 1]
    off = make(cfg=ServeConfig(jitter=0, retention=None), retention=lambda: ran.append(1))
    off.step(["linkedin"])
    assert ran == [1, 1]  # retention: null = never


def test_no_network_is_a_problem_but_no_stop(make):
    offline = {"sources": {"linkedin": {"status": "offline", "error": "no network: net::ERR_INTERNET_DISCONNECTED",
                                        "outcomes": {}, "pages_loaded": 0, "caught_up": False, "goal_hit": None}}}
    s = make(collect=lambda src, g: (offline, 0))
    r = s.step(["linkedin"])
    assert r.problems == ["linkedin: offline — no network: net::ERR_INTERNET_DISCONNECTED"]
    assert not s.state.read().get("stopped") and r.next_round_at == START + timedelta(minutes=40)
