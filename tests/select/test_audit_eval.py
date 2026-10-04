"""S4: audit draws, the evaluation numbers, the audit keys in the app, `filter eval`, and the S4 migration."""

import random

import pytest
from typer.testing import CliRunner

from auto_app.core.db.store import DbStore
from auto_app.process.job_browser import JobBrowserApp
from auto_app.select import audit
from auto_app.select.evaluate import clean_audits_needed, evaluate, exact_upper, format_eval, wilson
from auto_app.settings import Paths
from tests.select.test_review import env, ids  # noqa: F401 — env is a fixture


def test_bounds_match_the_worked_example():
    assert exact_upper(0, 60) == pytest.approx(0.0487, abs=5e-4) and exact_upper(0, 30) == pytest.approx(0.095, abs=1e-3)
    assert [clean_audits_needed(k, 0, 0.05) for k in range(4)] == [59, 93, 124, 153]  # study.md
    assert clean_audits_needed(0, 60, 0.05) == 0 and clean_audits_needed(1, 22, 0.05) == 71
    lo, hi = wilson(0, 60)
    assert lo == 0 and hi == pytest.approx(0.060, abs=1e-3)  # two-sided Wilson is wider: describing, not testing
    assert exact_upper(0, 0) == 1.0 and exact_upper(3, 3) == 1.0


def test_draws_are_random_stored_and_exclude_labelled_or_sampled_jobs(env):  # noqa: F811
    db, rv, jobs, _ = env  # ml said no to sales + analyst; research said no to ml + sales
    assert set(audit.pool(db, "ml")) == {jobs["sales"], jobs["analyst"]}
    rv.label(jobs["analyst"], "ml", False)  # already labelled → not in the pool
    assert audit.pool(db, "ml") == [jobs["sales"]]
    d = audit.draw(db, "ml", 5, "v1", rng=random.Random(1))
    assert d.jobs == [jobs["sales"]] and d.pool_size == 1
    assert audit.pool(db, "ml") == [] and audit.draw(db, "ml", 5) is None  # sampled → not drawn again
    [row] = audit.draws_of(db, "ml")
    assert (row["pool_size"], row["size"], row["filter_version"]) == (1, 1, "v1")
    assert audit.audit_status(db) == {jobs["sales"]: {"ml": None}} and audit.unlabeled_count(db, "ml") == 1
    rv.label(jobs["sales"], "ml", True)  # a miss: a good job ml rejected
    assert audit.audit_status(db) == {jobs["sales"]: {"ml": True}} and audit.unlabeled_count(db, "ml") == 0


def test_evaluation_numbers(env):  # noqa: F811
    db, rv, jobs, _ = env
    [e] = evaluate(db, ["ml"], 0.05)
    assert (e.judged, e.picks, e.no, e.precision.n, e.noise.n) == (3, 1, 2, 0, 0)
    assert e.recall() is None and not e.training_ok and e.more_audits == 59
    rv.label(jobs["ml"], "ml", True)  # its one pick: good
    audit.draw(db, "ml", 5, rng=random.Random(0))  # both "no" jobs sampled
    rv.label(jobs["sales"], "ml", False)
    rv.label(jobs["analyst"], "ml", True)  # one miss
    [e] = evaluate(db, ["ml"], 0.05)
    assert (e.precision.k, e.precision.n, e.noise.k, e.noise.n) == (1, 1, 1, 2)
    assert e.lost() == pytest.approx(1.0) and e.recall() == pytest.approx(0.5)  # 1 good pick / (1 + 1 lost)
    assert e.recall("lo") < 0.5 < e.recall("hi") and e.review_load == pytest.approx(1 / 3)
    text = format_eval([e])
    assert "precision      100%" in text and "1 missed → ≈1 missed in all" in text and "recall         ≈50%" in text
    assert "training data  NO — noise could be" in text
    # labels on a non-audited "no" job (e.g. you labelled it in Un-recommended) don't count as audit: not random
    [r] = evaluate(db, ["research"], 0.05)
    assert r.noise.n == 0


async def test_audit_tab_keys_and_live_panel(env):  # noqa: F811
    db, rv, jobs, _ = env
    rv.draw(5)  # every filter's "no" jobs: ml → sales, analyst; research → ml, sales
    app = JobBrowserApp(rv, audit_mode=True, dark=lambda: False)
    async with app.run_test(size=(180, 50)) as pilot:
        assert ids(app.rows) == set(jobs.values())
        tabs = str(app.query_one("#tabs").render())
        assert "Audit (3 left of 3)" in tabs and "Recommended" not in tabs and "All" not in tabs  # only drawn jobs
        await pilot.press("tab")
        assert app.kind == "audit"  # nowhere else to go
        panel = str(app.query_one("#eval").render())
        assert "ml  (as used)" in panel and "research  (as used)" in panel and "2 waiting" in panel
        while app.current()["job_id"] != jobs["sales"]:
            await pilot.press("down")
        await pilot.press("B")  # bad for both filters: both rightly said no
        fb = db.feedback_of(jobs["sales"])
        assert (fb["ml"]["label"], fb["research"]["label"]) == (False, False)
        assert "Audit (2 left of 3)" in str(app.query_one("#tabs").render())
        assert "1 audited, 0 missed" in str(app.query_one("#eval").render())  # the panel updated at once
        await pilot.press("u")
        assert db.feedback_of(jobs["sales"]) == {}  # one undo for both labels
        app.query_one("DataTable").move_cursor(row=[j["job_id"] for j in app.rows].index(jobs["analyst"]))
        await pilot.press("g")  # analyst: a good job ml rejected — a miss
        assert db.feedback_of(jobs["analyst"])["ml"]["label"] is True
        assert app.cells(next(j for j in app.rows if j["job_id"] == jobs["analyst"]), {})[0] == "✗"
        await pilot.press("e")
        assert not app.show_eval
        await pilot.press("d")  # nothing left to draw
        assert "nothing left to draw" in str(app.query_one("#status").render())
        await pilot.press("q")


def test_cli_filter_eval_and_audit_checks(env, tmp_path, monkeypatch):  # noqa: F811
    from auto_app.cli import app

    db, rv, jobs, folder = env
    monkeypatch.chdir(tmp_path)
    rv.label(jobs["ml"], "ml", True)
    r = CliRunner().invoke(app, ["filter", "eval", "--db", str(db.db_path), "--all-versions"])
    assert r.exit_code == 0, r.output
    assert "ml  (as used)" in r.output and "ml  (v " in r.output and "one-sided 95% exact" in r.output
    r = CliRunner().invoke(app, ["filter", "audit", "--db", str(db.db_path), "--dir", str(folder)])
    assert r.exit_code == 2 and "full-screen" in r.output and audit.total_items(db) == 0  # nothing drawn
    r = CliRunner().invoke(app, ["filter", "audit", "--db", str(db.db_path), "-f", "nope"])
    assert r.exit_code == 2


def test_migration_converts_ratings_and_frees_archived_identities(tmp_path):
    from alembic import command
    from alembic.config import Config

    from auto_app.core.db.engine import MIGRATIONS, make_engine

    engine = make_engine(tmp_path / "old.db")
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS))
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "7d2ba831d6e3")  # the schema before S4
    ts = "'2026-10-01 00:00:00'"
    with engine.begin() as conn:
        for job, verdict, correct in ((1, 1, 1), (2, 1, 0), (3, 0, 1), (4, 0, 0), (5, "NULL", 1)):
            conn.exec_driver_sql(f"INSERT INTO feedback (job_id, filter, verdict, correct, origin, created_at) "
                                 f"VALUES ({job}, 'f', {verdict}, {correct}, 'explicit', {ts})")
        conn.exec_driver_sql(f"INSERT INTO jobs (job_id, archived_at, last_seen_at) VALUES (9, {ts}, {ts})")
        conn.exec_driver_sql(f"INSERT INTO postings (posting_id, source, source_job_id, kind, state, first_seen_at, "
                             f"last_seen_at, job_id, purged) VALUES (1, 's', 'X1', 'api', 'archived', {ts}, {ts}, 9, 0)")
    store = DbStore(tmp_path / "old.db", Paths(home=tmp_path))  # opening upgrades
    assert {j: store.feedback_of(j)["f"]["label"] for j in range(1, 6)} == {1: True, 2: False, 3: False, 4: True, 5: None}
    assert not store.posting_exists("s", "X1") and store.posting_exists("s", "X1#archived9")


async def test_job_browse_has_no_audit_tab_and_cannot_draw(env):  # noqa: F811
    db, rv, jobs, _ = env
    rv.draw(1)  # one per filter: each still has a "no" job left to draw
    app = JobBrowserApp(rv, dark=lambda: False)
    async with app.run_test(size=(180, 50)) as pilot:
        assert "Audit" not in str(app.query_one("#tabs").render())
        await pilot.press("d")
        assert audit.total_items(db) == 2  # nothing more drawn outside `filter audit`
        await pilot.press("q")


async def test_after_labelling_the_cursor_goes_to_the_next_unlabelled_job(env):  # noqa: F811
    db, rv, jobs, _ = env
    rv.draw(5)  # ml → sales, analyst; research → ml, sales (sales: sampled for both filters)
    app = JobBrowserApp(rv, audit_mode=True, dark=lambda: False)
    async with app.run_test(size=(180, 50)) as pilot:
        order = [j["job_id"] for j in app.rows]
        app.query_one("DataTable").move_cursor(row=order.index(jobs["sales"]))
        await pilot.pause()
        names = [v.filter for v in app.detail.verdicts]
        app.filter_index = names.index("ml")
        await pilot.press("b")  # sales: labelled for ml, research still to do → stay, research picked
        assert app.current()["job_id"] == jobs["sales"] and names[app.filter_index] == "research"
        await pilot.press("b")  # sales done → the next still-to-label job, not where sales moved to
        nxt = app.current()["job_id"]
        assert nxt != jobs["sales"] and None in next(j for j in app.rows if j["job_id"] == nxt)["audit"].values()
        await pilot.press("B")
        await pilot.press("B")  # the last one
        assert all(None not in j["audit"].values() for j in app.rows)
        assert "all labelled — d draws more" in str(app.query_one("#status").render())
        await pilot.press("q")


async def test_each_app_shows_only_its_own_keys(env):  # noqa: F811
    db, rv, jobs, _ = env
    rv.draw(5)
    app = JobBrowserApp(rv, audit_mode=True, dark=lambda: False)
    async with app.run_test(size=(200, 50)) as pilot:
        names = {b.binding.action.split("(")[0] for b in app.active_bindings.values()}
        shown = {n for n in names if app.check_action(n, ()) is not False}
        assert {"label", "label_all", "draw", "toggle_eval", "undo", "filter", "open_link", "quit"} <= shown
        assert not shown & {"decide", "mark", "narrow", "next_list"}
        before = db.job(app.current()["job_id"])["decision"]
        await pilot.press("a", "n", "m")  # do nothing in an audit
        assert db.job(app.current()["job_id"])["decision"] == before and len(app.screen_stack) == 1
        await pilot.press("q")
    app = JobBrowserApp(rv, dark=lambda: False)
    async with app.run_test(size=(200, 50)) as pilot:
        assert app.check_action("draw", ()) is False and app.check_action("decide", ()) is True
        await pilot.press("q")


def test_evaluation_splits_misses_by_cause(env):  # noqa: F811
    from auto_app.core.db import tables as T

    db, rv, jobs, _ = env  # ml said no to sales and analyst
    with db.engine.begin() as conn:  # ml's "no" on sales was for a dealbreaker; on analyst, a poor fit
        conn.execute(T.judgments.update().where(T.judgments.c.job_id == jobs["sales"], T.judgments.c.filter == "ml")
                     .values(dealbreaker=True))
        conn.execute(T.judgments.update().where(T.judgments.c.job_id == jobs["analyst"], T.judgments.c.filter == "ml")
                     .values(fit=False, dealbreaker=False))
    audit.draw(db, "ml", 5, rng=random.Random(0))
    rv.label(jobs["sales"], "ml", True)  # it should have recommended it: a wrong dealbreaker call
    rv.label(jobs["analyst"], "ml", False)
    [e] = evaluate(db, ["ml"], 0.05)
    assert e.no_dealbreaker == 1 and (e.noise_dealbreaker.k, e.noise_dealbreaker.n) == (1, 1)
    assert (e.noise_fit.k, e.noise_fit.n) == (0, 1)
    assert "by cause       dealbreaker: 1 said no, 1 audited, 1 missed" in "\n".join(e.lines())
    [v] = [v for v in rv.detail(jobs["sales"]).verdicts if v.filter == "ml"]
    from auto_app.process.job_browser import _verdict

    assert _verdict(v) == "no — dealbreaker, doesn't fit"  # (the fixture's older answer: fit = no)
