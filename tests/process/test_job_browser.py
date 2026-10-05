"""`job browse`: tabs per state, source / company narrowing, marking with undo, archived read-only; the app via its pilot."""

import pytest
from typer.testing import CliRunner

from tests.ui_helpers import ui_app
from auto_app.core.db.job_ttl import apply_job_ttl
from auto_app.process.job_browser import JobBrowser, StateMenu
from auto_app.select.filters import load_filter
from tests.core.test_job_ttl import seen_days_ago
from tests.select.test_review import TTL, env, ids  # noqa: F401 — env is a fixture


@pytest.fixture
def browser(env):  # noqa: F811
    db, _, jobs, folder = env
    db.mark_job(jobs["analyst"], "applied")
    seen_days_ago(db, "linkedin/L3", 400)
    apply_job_ttl(db, TTL)  # the applied analyst job becomes an archived record
    return db, JobBrowser(db, [load_filter(folder / "ml"), load_filter(folder / "research")], ttl=TTL), jobs


def test_tabs_narrowing_and_counts(browser):
    db, b, jobs = browser
    assert ids(b.jobs("all")) == {jobs["ml"], jobs["sales"]}
    assert ids(b.jobs("recommended")) == {jobs["ml"]} and ids(b.jobs("un_recommended")) == {jobs["sales"]}
    assert ids(b.jobs("archived")) == {jobs["analyst"]}
    assert b.counts() == {"all": 2, "not_evaluated": 0, "recommended": 1, "all_recommended": 1, "un_recommended": 1,
                          "want_to_apply": 0, "applied": 0, "not_applied": 0, "archived": 1, "audit": 0, "audit_total": 0}
    db.mark_job(jobs["ml"], "want_to_apply")  # decided: out of New recommended, still in All recommended
    assert ids(b.jobs("recommended")) == set() and ids(b.jobs("all_recommended")) == {jobs["ml"]}
    db.mark_job(jobs["ml"], "applied")
    assert ids(b.jobs("applied")) == {jobs["ml"]} and ids(b.jobs("all_recommended")) == {jobs["ml"]}
    assert ids(b.jobs("all", search="nvidia")) == {jobs["ml"], jobs["sales"]}  # sources are searched too
    b.sources = ["linkedin"]
    assert ids(b.jobs("all")) == set() and ids(b.jobs("archived")) == {jobs["analyst"]}
    b.sources, b.company = [], "acme"
    assert b.counts()["archived"] == 1 and b.counts()["all"] == 0


def test_mark_keeps_in_tab_and_undo(browser):
    db, b, jobs = browser
    b.mark(jobs["sales"], "applied")
    j = db.job(jobs["sales"])
    assert (j["state"], j["step2"]) == ("applied", "un_recommended")
    assert ids(b.jobs("un_recommended")) == set() and ids(b.jobs("un_recommended", keep={jobs["sales"]})) == {jobs["sales"]}
    assert b.undo() == f"state of #{jobs['sales']}"
    assert db.job(jobs["sales"])["state"] == "un_recommended"
    with pytest.raises(ValueError, match="archived"):
        b.mark(jobs["analyst"], "not_applied")
    d = b.detail(jobs["analyst"])  # archived: the description kept with the record
    assert "Data Analyst" in d.text_for_labels


async def test_app_tabs_mark_menu_and_read_only(browser):
    db, b, jobs = browser
    app = ui_app(b, open_url=lambda u: None, dark=lambda: False, start="all")
    async with app.run_test(size=(180, 45)) as pilot:
        s = app.screen
        assert s.kind == "all" and ids(s.rows) == {jobs["ml"], jobs["sales"]}
        while s.current()["job_id"] != jobs["sales"]:
            await pilot.press("down")
        detail = str(s.query_one("#detail").render())
        assert "postings (1)" in detail and "matching keys" in detail and "not engineering" in detail
        await pilot.press("m")
        assert isinstance(app.screen, StateMenu)
        await pilot.press("a")  # inside the menu: not the app's "want to apply"
        assert db.job(jobs["sales"])["decision"] is None
        await pilot.press("down", "down", "enter")  # want to apply → applied → not applied
        assert db.job(jobs["sales"])["state"] == "not_applied"
        await pilot.press("u")
        assert db.job(jobs["sales"])["state"] == "un_recommended"
        await pilot.press("s", *"linkedin", "enter")
        assert s.rows == [] and "source: linkedin" in str(s.query_one("#tabs").render())
        await pilot.press("s", "ctrl+u", "enter")  # the box shows the current sources; cleared = every source
        assert len(s.rows) == 2
        await pilot.press("shift+tab")  # wraps to the last tab
        assert s.kind == "not_applied" and s.tabs == ("all", "not_evaluated", "recommended", "all_recommended",
                                                      "un_recommended", "applied", "not_applied")
        tabs = str(s.query_one("#tabs").render())
        assert "New recommended" in tabs and "All recommended" in tabs and "Archived" not in tabs and "Want to apply" not in tabs
        await pilot.press("q")


def test_cli_job_browse_checks(browser, tmp_path, monkeypatch):
    from auto_app.cli import app

    db, _, _ = browser
    monkeypatch.chdir(tmp_path)
    r = CliRunner().invoke(app, ["job", "browse", "--state", "nope", "--db", str(db.db_path)])
    assert r.exit_code == 2 and "expected one of" in r.output
    r = CliRunner().invoke(app, ["job", "browse", "--db", str(db.db_path)])
    assert r.exit_code == 2 and "full-screen" in r.output
