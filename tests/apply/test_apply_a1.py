"""Step 3 A1: profile / answers / policy checks, platforms, the queue and chain runner (fallback, retries,
hand-offs), your answers in the inbox, the CLI."""

import shutil

import pytest
from typer.testing import CliRunner

from tests.ui_helpers import inbox_app
from auto_app.apply.appliers import Applier, platform_of, registry
from auto_app.apply.policy import ApplyPolicy, check_policy, load_policy
from auto_app.apply.profile import load_me
from auto_app.apply.queue import Applications
from auto_app.settings import PROJECT_ROOT
from tests.select.test_review import TTL, env  # noqa: F401 — env is a fixture

EXAMPLE = PROJECT_ROOT / "profile.example"


def me_home(tmp_path, cv=True):
    shutil.copytree(EXAMPLE, tmp_path / "profile")
    if cv:
        (tmp_path / "profile" / "cv.pdf").write_bytes(b"%PDF-1.4")
    return tmp_path


def test_profile_checks(tmp_path):
    me = load_me(me_home(tmp_path))
    assert me.problems == [] and me.missing == [] and me.profile.name.first == "Alex"
    assert me.answers["why_company"].kind == "free_text" and me.cv.name == "cv.pdf"
    (tmp_path / "profile" / "cv.pdf").unlink()
    (tmp_path / "profile" / "answers.yaml").write_text("relocation: {question: q, answer: yes}\nbad: {answer: x}\n")
    me = load_me(tmp_path)
    assert any("CV not found" in p for p in me.problems) and any("bad: question" in p for p in me.problems)
    assert "requires_sponsorship" in me.missing and "relocation" not in me.missing
    empty = load_me(tmp_path / "nowhere")
    assert any("profile.yaml missing" in p for p in empty.problems) and any("answers.yaml missing" in p for p in empty.problems)


def test_policy_defaults_checks_and_example(tmp_path):
    p, err = load_policy(tmp_path / "none.yaml")
    assert err is None and p.chain("greenhouse") == ["simplify_auto", "greenhouse", "simplify", "manual"]
    assert p.chain("other") == ["simplify_auto", "simplify", "manual"] and p.chain("linkedin_easy_apply") == ["simplify", "manual"]
    assert load_policy(PROJECT_ROOT / "config" / "apply.example.yaml") == (ApplyPolicy(), None)  # the template = the defaults
    bad = ApplyPolicy(appliers={"linkedin_easy_apply": ["greenhouse", "manual"], "nowhere": ["simplify"], "*": ["zap"]})
    problems = check_policy(bad, {"simplify", "manual"}, {"greenhouse"})
    assert any("never automated" in x for x in problems) and any("unknown platform" in x for x in problems)
    assert any("unknown applier 'zap'" in x for x in problems) and any("should end with `manual`" in x for x in problems)
    (tmp_path / "apply.yaml").write_text("workers: 0\n")
    p, err = load_policy(tmp_path / "apply.yaml")
    assert err and "workers" in err and p == ApplyPolicy()


def test_platforms():
    assert [platform_of(u) for u in (
        "https://boards.greenhouse.io/acme/jobs/1", "https://job-boards.greenhouse.io/acme/jobs/2",
        "https://jobs.lever.co/acme/abc", "https://jobs.ashbyhq.com/acme/x", "https://nvidia.wd5.myworkdayjobs.com/x",
        "https://www.linkedin.com/jobs/view/1/", "https://careers.acme.example/1", None)] == \
        ["greenhouse", "greenhouse", "lever", "ashby", "workday", "linkedin_easy_apply", "other", "other"]


@pytest.fixture
def apps(env):  # noqa: F811
    db, rv, jobs, _ = env
    for key in ("ml", "analyst"):
        db.mark_job(jobs[key], "want_to_apply")
    opened = []
    policy = ApplyPolicy(appliers={"*": ["lever", "simplify", "manual"]})  # lever isn't built yet
    a = Applications(db, policy, registry(opener=opened.append, manual_opener=opened.append, revealer=opened.append), ttl=TTL)
    return db, a, jobs, opened


def test_run_falls_back_through_the_chain_to_a_handoff(apps):
    db, a, jobs, opened = apps
    q = a.queue()
    assert {j["job_id"] for j in q} == {jobs["ml"], jobs["analyst"]}
    assert {j["platform"] for j in q} == {"workday", "other"} and all(j["application"] is None for j in q)
    assert a.run() == {"handoff": 2}
    [app] = [r for r in a.rows() if r["job_id"] == jobs["ml"]]
    assert (app["status"], app["applier"], app["chain_pos"]) == ("handoff", "simplify", 1)
    tried = a.attempts(app["application_id"])
    assert [(t["applier"], t["outcome"]) for t in tried] == [("lever", "unsupported"), ("simplify", "handoff")]
    assert "not built yet (A4)" in tried[0]["reason"]
    assert a.run() == {}  # nothing new to start
    assert "Simplify" in a.open(app["application_id"]) and opened == [app["apply_url"]]


def test_your_answers_on_handoffs_and_failures(apps, tmp_path):
    db, a, jobs, opened = apps
    a.run()
    ml = next(r for r in a.rows() if r["job_id"] == jobs["ml"])["application_id"]
    an = next(r for r in a.rows() if r["job_id"] == jobs["analyst"])["application_id"]
    assert a.couldnt(ml) == "handoff" and a.get(ml)["applier"] == "manual"  # next applier
    cv = tmp_path / "cv.pdf"
    a.open(ml, cv)
    assert opened[-2:] == [a.get(ml)["apply_url"], cv]  # the link and your CV
    assert a.couldnt(ml) == "failed"  # the chain is exhausted
    assert a.enqueue() == []  # a failed application isn't reopened by itself …
    assert a.retry(ml) == "handoff" and a.get(ml)["applier"] == "simplify"  # … you retry it
    assert a.retry(ml, "manual") == "handoff" and a.get(ml)["applier"] == "manual"
    a.applied(ml)
    assert a.get(ml)["status"] == "submitted" and db.job(jobs["ml"])["state"] == "applied"
    a.drop(an)
    assert a.get(an)["status"] == "dropped" and db.job(jobs["analyst"])["state"] == "not_applied"
    assert a.queue() == []
    db.mark_job(jobs["ml"], "want_to_apply")  # changed your mind (m in Jobs): queued again, not stuck in Done
    [j] = a.queue()
    assert j["job_id"] == jobs["ml"] and j["application"] is None
    [new] = a.enqueue()
    assert new != ml and a.get(ml)["status"] == "submitted"  # the old one stays as history

    a.couldnt(new)
    a.policy = ApplyPolicy(appliers={"*": ["simplify", "manual"]})  # the policy changed since the application started
    a.requeue(new)
    assert a.get(new)["chain"] == ["simplify", "manual"] and a.get(new)["status"] == "queued"  # today's chain


def test_a_failing_applier_is_retried_once_then_skipped(env):  # noqa: F811
    db, _, jobs, _ = env
    db.mark_job(jobs["ml"], "want_to_apply")

    class Flaky(Applier):
        name = "flaky"
        calls = 0

        def prepare(self, app, ctx=None):
            Flaky.calls += 1
            raise TimeoutError("form didn't load")

    reg = {**registry(opener=lambda u: None), "flaky": Flaky()}
    a = Applications(db, ApplyPolicy(appliers={"*": ["flaky", "manual"]}), reg, ctx=object())
    assert a.run() == {"handoff": 1} and Flaky.calls == 2
    [app] = a.rows()
    assert [(t["applier"], t["outcome"]) for t in a.attempts(app["application_id"])] == \
        [("flaky", "failed"), ("flaky", "failed"), ("manual", "handoff")]


async def test_inbox_tabs_keys_and_actions(apps):
    db, a, jobs, opened = apps
    a.run()
    app = inbox_app(a, dark=lambda: False, open_url=opened.append)
    async with app.run_test(size=(180, 40)) as pilot:
        s = app.screen
        assert s.kind == "handoff" and len(s.rows) == 2 and "Hand-offs (2)" in str(s.query_one("#tabs").render())
        assert s.check_action("retry", ()) and not s.check_action("manual", ())  # only this tab's keys
        assert "Simplify" in str(s.query_one("#detail").render())
        await pilot.press("o")
        assert len(opened) == 1
        first = s.current()
        await pilot.press("a")
        assert db.job(first["job_id"])["state"] == "applied" and len(s.rows) == 1
        await pilot.press("c", "c")  # simplify → manual → failed
        assert s.rows == []
        await pilot.press("tab")
        assert s.kind == "failed" and len(s.rows) == 1 and s.check_action("retry", ()) and not s.check_action("applied", ())
        await pilot.press("m")
        assert s.rows == []  # back to hand-offs, by hand
        await pilot.press("tab")
        assert s.kind == "done" and len(s.rows) == 1
        await pilot.press("q")


def test_cli(apps, tmp_path, monkeypatch):
    from auto_app.cli import app

    db, _, jobs, _ = apps
    monkeypatch.setenv("AUTO_APP_HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    r = CliRunner().invoke(app, ["apply", "profile"])
    assert r.exit_code == 1 and "profile.yaml missing" in r.output
    me_home(tmp_path)
    r = CliRunner().invoke(app, ["apply", "profile"])
    assert r.exit_code == 0 and "ready to apply" in r.output and "Alex Doe" in r.output
    r = CliRunner().invoke(app, ["apply", "policy"])
    assert r.exit_code == 0 and "greenhouse ✓" in r.output and "lever …" in r.output and "simplify ✓" in r.output and "the defaults" in r.output
    db_ = ["--db", str(db.db_path)]
    r = CliRunner().invoke(app, ["apply", "queue", *db_])
    assert r.exit_code == 0 and "2 queued" in r.output and "not started" in r.output
    r = CliRunner().invoke(app, ["apply", "run", "-j", str(jobs["ml"]), *db_])
    assert r.exit_code == 0 and "1 handoff" in r.output and "apply inbox" in r.output
    r = CliRunner().invoke(app, ["apply", "list", *db_])
    assert r.exit_code == 0 and "handoff" in r.output and "(simplify, 2 attempts)" in r.output  # simplify_auto (not built yet) skipped first
    r = CliRunner().invoke(app, ["apply", "inbox", *db_])
    assert r.exit_code == 2 and "full-screen" in r.output
