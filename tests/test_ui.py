"""`auto-app ui`: one app, three sections — F1 Jobs, F2 Audit, F3 Inbox; state kept per section, reload on return."""

from typer.testing import CliRunner

from auto_app.apply.appliers import registry
from auto_app.apply.inbox import InboxScreen
from auto_app.apply.policy import ApplyPolicy
from auto_app.apply.queue import Applications
from auto_app.process.job_browser import JobBrowser, JobsScreen, StateMenu
from auto_app.select.filters import load_filter
from auto_app.ui import AutoApp, MessageScreen
from tests.select.test_review import TTL, env  # noqa: F401 — env is a fixture


def make(env, inbox=True):  # noqa: F811
    db, rv, jobs, folder = env
    flts = [load_filter(folder / "ml"), load_filter(folder / "research")]
    apps = Applications(db, ApplyPolicy(), registry(opener=lambda u: None), ttl=TTL) if inbox else None
    app = AutoApp(jobs=JobBrowser(db, flts, ttl=TTL), audit=JobBrowser(db, flts, ttl=TTL), inbox=apps,
                  dark=lambda: False, open_url=lambda u: None, why={"inbox": "policy broken"})
    return db, jobs, apps, app


async def test_function_keys_switch_sections_and_keep_their_state(env):  # noqa: F811
    db, jobs, apps, app = make(env)
    async with app.run_test(size=(200, 50)) as pilot:
        assert isinstance(app.screen, JobsScreen) and not app.screen.audit_mode and app.section == "jobs"
        bar = str(app.screen.query_one("#sections").render())
        assert "Jobs" in bar and "Audit" in bar and "Apply" in bar and "F1 / F2 / F3" in bar
        jobs_screen = app.screen
        await pilot.press("slash", *"acme", "enter")  # a search in Jobs …
        await pilot.press("f2")
        assert app.section == "audit" and app.screen.audit_mode
        assert "Audit (0 left of 0)" in str(app.screen.query_one("#tabs").render())
        await pilot.press("d")  # draw a sample: the bar counts it
        assert "Audit (" in str(app.screen.query_one("#sections").render()) and " left)" in str(app.screen.query_one("#sections").render())
        await pilot.press("f3")
        assert isinstance(app.screen, InboxScreen)
        await pilot.press("f1")
        assert app.screen is jobs_screen and jobs_screen.search == "acme"  # … still there
        await pilot.press("q")


async def test_a_section_reloads_when_you_come_back(env):  # noqa: F811
    db, jobs, apps, app = make(env)
    async with app.run_test(size=(200, 50)) as pilot:
        s = app.screen
        while s.current()["job_id"] != jobs["ml"]:
            await pilot.press("down")
        await pilot.press("a")  # want to apply, in Jobs
        apps.run()  # (as `auto-app apply run` would, meanwhile)
        await pilot.press("f3")
        assert [r["job_id"] for r in app.screen.rows] == [jobs["ml"]]  # the inbox shows the new hand-off
        assert "Apply (1 waiting)" in str(app.screen.query_one("#sections").render())
        await pilot.press("q")


async def test_switching_is_ignored_while_a_popup_is_open_and_missing_sections_say_why(env):  # noqa: F811
    db, jobs, apps, app = make(env, inbox=False)
    async with app.run_test(size=(200, 50)) as pilot:
        await pilot.press("m")
        assert isinstance(app.screen, StateMenu)
        await pilot.press("f2")
        assert isinstance(app.screen, StateMenu) and app.section == "jobs"
        await pilot.press("escape", "f3")
        assert isinstance(app.screen, MessageScreen) and "policy broken" in str(app.screen.query_one("#why").render())
        names = [c.title for c in app.get_system_commands(app.screen)]
        assert {"Go to Jobs", "Go to Audit", "Go to Apply"} <= set(names)
        await pilot.press("q")


def test_cli_ui_needs_a_terminal(env, tmp_path, monkeypatch):  # noqa: F811
    from auto_app.cli import app

    db = env[0]
    monkeypatch.chdir(tmp_path)
    r = CliRunner().invoke(app, ["ui", "--db", str(db.db_path)])
    assert r.exit_code == 2 and "full-screen" in r.output


async def test_click_a_section_or_a_tab_and_brackets_cycle(env):  # noqa: F811
    db, jobs, apps, app = make(env)
    async with app.run_test(size=(200, 50)) as pilot:
        bar = str(app.screen.query_one("#sections").render())
        assert "[ ] to switch" in bar
        await pilot.click("#sections", offset=(bar.index("Apply") + 1, 0))  # click "Apply"
        assert app.section == "inbox"
        tabs = str(app.screen.query_one("#tabs").render())
        await pilot.click("#tabs", offset=(tabs.index("Done") + 1, 0))  # click a tab
        assert app.screen.kind == "done"
        await pilot.press("right_square_bracket")  # next section wraps to Jobs
        assert app.section == "jobs"
        tabs = str(app.screen.query_one("#tabs").render())
        await pilot.click("#tabs", offset=(tabs.index("Un-recommended") + 1, 0))
        assert app.screen.kind == "un_recommended"
        await pilot.press("left_square_bracket")
        assert app.section == "inbox"
        await pilot.press("f1", "slash", "[", "x", "enter")  # in a search box, [ is typed, not a switch
        assert app.section == "jobs" and app.screen.search == "[x"
        await pilot.press("q")


async def test_question_mark_lists_the_sections_keys(env):  # noqa: F811
    from auto_app.ui import HelpScreen

    db, jobs, apps, app = make(env)
    async with app.run_test(size=(200, 50)) as pilot:
        keys = str(app.screen.query_one("#keys").render())  # the key bar: the main keys only, ? for the rest
        assert "a want to apply" in keys and "? all keys" in keys and "m set state" not in keys and "palette" not in keys
        await pilot.press("question_mark")
        assert isinstance(app.screen, HelpScreen)
        rows = dict(app.screen.rows)
        assert "add a short reason" in rows["r"] and "want to apply" in rows["a"] and rows["/"].startswith("search")
        assert "1–9" in rows and "switch section" in " ".join(rows.values())  # keys kept off the footer are listed
        await pilot.press("f2")  # switching is ignored while help is open
        assert isinstance(app.screen, HelpScreen)
        await pilot.press("escape", "f2", "question_mark")
        rows = dict(app.screen.rows)
        assert "d" in rows and "a" not in rows and "m" not in rows  # audit: only audit keys
        await pilot.press("escape", "f3", "question_mark")
        rows = dict(app.screen.rows)
        texts = " | ".join(rows.values())
        assert "start this job" in texts and "R" in rows and "A" not in rows and "c" not in rows  # inbox, Queued: its keys only
        await pilot.press("escape", "f1", "slash", "question_mark", "enter")  # ? is typed in a search box
        assert app.screen.search == "?"
        await pilot.press("q")


async def test_queue_in_jobs_then_start_in_the_inbox(env):  # noqa: F811
    db, jobs, apps, app = make(env)
    async with app.run_test(size=(200, 50)) as pilot:
        s = app.screen
        while s.current()["job_id"] != jobs["ml"]:
            await pilot.press("down")
        await pilot.press("a")
        assert "→ Apply (F3) → Queued" in str(s.query_one("#status").render())
        assert jobs["ml"] not in [j["job_id"] for j in s.rows]  # out of New recommended at once
        await pilot.press("f3")
        inbox = app.screen
        assert inbox.kind == "queued" and [r["job_id"] for r in inbox.rows] == [jobs["ml"]]  # right away, before starting
        assert "Apply (1 queued)" in str(inbox.query_one("#sections").render())
        await pilot.press("R")
        app.screen.start_thread.join(10)
        await pilot.pause()
        assert inbox.kind == "handoff" and [r["job_id"] for r in inbox.rows] == [jobs["ml"]]  # a Workday job → Simplify
        await pilot.press("q")


async def test_quitting_never_waits_for_starting_jobs_and_warns_during_a_submission(env):  # noqa: F811
    import time

    db, jobs, apps, app = make(env)
    db.mark_job(jobs["ml"], "want_to_apply")
    real_run = apps.run
    apps.run = lambda *a, **k: (time.sleep(3), real_run(*a, **k))[1]  # a slow start (like Claude)
    app.start = "inbox"
    t0 = time.monotonic()
    async with app.run_test(size=(200, 50)) as pilot:
        await pilot.press("R")
        await pilot.press("q")
        await pilot.pause()
        assert not app.is_running
    assert time.monotonic() - t0 < 2.5  # didn't wait for the start to finish

    db2, jobs2, apps2, app2 = make(env)
    app2.start = "inbox"
    async with app2.run_test(size=(200, 50)) as pilot:
        app2.screen.busy.add(1)  # a submission in progress
        await pilot.press("q")
        assert app2.is_running and "press q again" in str(app2.screen.query_one("#status").render())
        await pilot.press("q")
        await pilot.pause()
        assert not app2.is_running


async def test_queued_tab_enter_starts_one_and_d_unqueues(env):  # noqa: F811
    db, jobs, apps, app = make(env)
    for k in ("ml", "analyst"):
        db.mark_job(jobs[k], "want_to_apply")
    app.start = "inbox"
    async with app.run_test(size=(200, 50)) as pilot:
        s = app.screen
        assert s.kind == "queued" and {r["job_id"] for r in s.rows} == {jobs["ml"], jobs["analyst"]}
        first = s.current()["job_id"]
        await pilot.press("enter")  # start just this one
        s.start_thread.join(10)
        await pilot.pause()
        assert [r["job_id"] for r in apps.rows()] == [first]
        while s.kind != "queued":
            await pilot.press("tab")
        other = s.current()["job_id"]
        await pilot.press("d")
        assert db.job(other)["state"] == "not_applied" and s.rows == []
        await pilot.press("q")
