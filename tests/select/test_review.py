"""S3: review in `job browse` — lists, a job's details, decisions, per-filter feedback, undo, reasons; the app via its pilot."""

import shutil

import pytest
from typer.testing import CliRunner

from auto_app.core.config import AppConfig, JobTTL
from auto_app.core.db.job_ttl import apply_job_ttl
from auto_app.core.db.store import DbStore
from auto_app.select.engine import SelectOptions, run_select
from auto_app.select.filters import EXAMPLES_DIR, load_filter
from auto_app.process.job_browser import JobBrowser, JobBrowserApp
from auto_app.select.review import Review
from auto_app.select.runner import api_runner
from auto_app.settings import Paths
from tests.core.test_job_ttl import seen_days_ago
from tests.process.test_link import OTHER, add, job_of, run, wd
from tests.select.fake_model import FakeClient, answer, prompt_of, response, server_tool, text

CFG = AppConfig()
TTL = JobTTL()


def ml_says(kwargs):
    p = prompt_of(kwargs)
    if "Title: ML Engineer" in p:
        return response(server_tool("web_search", {"query": "nvidia h-1b"}),
                        text('{"recommend": true, "reason": "strong ML fit"}'))
    if "Title: Sales Lead" in p:
        return answer(False, "not engineering")
    return answer(False, "analyst role")


def research_says(kwargs):
    return answer("Title: Data Analyst" in prompt_of(kwargs), "research view")


@pytest.fixture
def env(tmp_path):
    db = DbStore(tmp_path / "data" / "auto_app.db", Paths(home=tmp_path))
    add(db, "nvidia", "JR1", url=wd("JR1"), title="ML Engineer")
    add(db, "nvidia", "JR2", url=wd("JR2"), title="Sales Lead", desc=OTHER)
    add(db, "linkedin", "L3", apply_url="https://acme.example/jobs/3", title="Data Analyst", company="Acme",
        desc=OTHER.replace("sell", "report"))
    run(db)
    folder = tmp_path / "filters"
    shutil.copytree(EXAMPLES_DIR / "ml-engineer", folder / "ml")
    shutil.copytree(EXAMPLES_DIR / "ml-engineer", folder / "research")
    (folder / "research" / "command.md").write_text((folder / "research" / "command.md").read_text() + "\nResearch roles.\n")
    ml, research = load_filter(folder / "ml"), load_filter(folder / "research")
    by_filter = {"ml": api_runner(FakeClient(ml_says)), "research": api_runner(FakeClient(research_says))}
    run_select(db, CFG, [ml, research], lambda flt, prompt, **kw: by_filter[flt.name](flt, prompt, **kw), SelectOptions())
    jobs = {t: job_of(db, ref) for t, ref in (("ml", "nvidia/JR1"), ("sales", "nvidia/JR2"), ("analyst", "linkedin/L3"))}
    return db, JobBrowser(db, [ml, research], ttl=TTL), jobs, folder


def ids(rows):
    return {j["job_id"] for j in rows}


def test_lists_detail_and_filter_scope(env):
    db, rv, jobs, _ = env
    assert ids(rv.jobs("recommended")) == {jobs["ml"], jobs["analyst"]}  # any filter recommends
    assert ids(rv.jobs("un_recommended")) == {jobs["sales"]}
    assert {k: rv.counts()[k] for k in ("recommended", "un_recommended", "want_to_apply")} == \
        {"recommended": 2, "un_recommended": 1, "want_to_apply": 0}
    assert ids(rv.jobs("recommended", search="acme")) == {jobs["analyst"]}
    d = rv.detail(jobs["ml"])
    assert [(v.filter, v.recommend, v.current) for v in d.verdicts] == [("ml", True, True), ("research", False, True)]
    assert d.verdicts[0].reason == "strong ML fit" and "web_search" in d.verdicts[0].lookups[0]
    assert "ML Engineer" in d.text_for_labels and d.description
    assert JobBrowser(db, [], only_filters=["other"]).jobs("all") == []  # -f: only jobs those filters judged
    only = Review(db, [], only_filters=["research"])
    assert [v.filter for v in only.detail(jobs["ml"]).verdicts] == ["research"]
    assert [v.current for v in only.detail(jobs["ml"]).verdicts] == [False]  # its folder isn't loaded


def test_decide_sets_state_records_review_and_never_expires(env):
    db, rv, jobs, _ = env
    rv.decide(jobs["ml"], "want_to_apply", "great team")
    j = db.job(jobs["ml"])
    assert (j["state"], j["step2"], j["decision"], j["expires_at"]) == ("want_to_apply", "recommended", "want_to_apply", None)
    [r] = db.reviews(jobs["ml"])
    assert r["decision"] == "want_to_apply" and r["reason"] == "great team" and "ML Engineer" in r["job_text"]
    assert {f["filter"] for f in r["filters"]} == {"ml", "research"}
    assert ids(rv.jobs("recommended")) == {jobs["analyst"]} and ids(rv.jobs("want_to_apply")) == {jobs["ml"]}
    assert ids(rv.jobs("recommended", keep={jobs["ml"]})) == {jobs["ml"], jobs["analyst"]}  # answered this session
    # want to apply: the filter that said no (research) is rated wrong, implied by your decision
    assert db.feedback_of(jobs["ml"])["research"]["origin"] == "implied"
    seen_days_ago(db, "nvidia/JR1", 400)
    rep = apply_job_ttl(db, TTL)
    assert jobs["ml"] not in {e.job_id for e in rep.deleted + rep.archived}  # queued: never expires
    rv.decide(jobs["analyst"], "not_applied")
    assert db.job(jobs["analyst"])["state"] == "not_applied"
    with pytest.raises(ValueError):
        rv.decide(jobs["sales"], "applied")  # only Step 3 sets applied


def test_apply_on_un_recommended_rates_the_no_filters_wrong_unless_rated(env):
    db, rv, jobs, _ = env
    rv.rate(jobs["sales"], "research", True, "fine to skip")  # your own rating wins
    rv.decide(jobs["sales"], "want_to_apply")
    fb = db.feedback_of(jobs["sales"])
    assert (fb["ml"]["correct"], fb["ml"]["origin"], fb["ml"]["verdict"]) == (False, "implied", False)
    assert (fb["research"]["correct"], fb["research"]["origin"]) == (True, "explicit")
    assert db.job(jobs["sales"])["step2"] == "un_recommended"  # Step 2's verdict is kept
    assert rv.undo() == f"decision on #{jobs['sales']}"
    assert set(db.feedback_of(jobs["sales"])) == {"research"} and db.job(jobs["sales"])["decision"] is None
    assert db.reviews(jobs["sales"]) == []


def test_rate_upserts_per_filter_with_version_and_undo_restores(env):
    db, rv, jobs, folder = env
    rv.rate(jobs["ml"], "ml", True)
    rv.rate(jobs["ml"], "ml", False)
    row = db.feedback_of(jobs["ml"])["ml"]
    assert row["correct"] is False and row["verdict"] is True and row["filter_version"] == load_filter(folder / "ml").version
    assert rv.add_reason(jobs["ml"], "too senior") == "feedback (ml)"
    assert db.feedback_of(jobs["ml"])["ml"]["reason"] == "too senior"
    rv.undo()
    assert db.feedback_of(jobs["ml"])["ml"]["correct"] is True  # back to the first rating
    rv.undo()
    assert db.feedback_of(jobs["ml"]) == {} and rv.undo() is None
    with pytest.raises(ValueError, match="didn't judge"):
        rv.rate(jobs["ml"], "nope", True)
    assert rv.marks() == {}


async def test_app_keys(env):
    db, rv, jobs, _ = env
    opened = []
    app = JobBrowserApp(rv, open_url=opened.append, start="recommended", dark=lambda: False)
    async with app.run_test(size=(160, 40)) as pilot:
        assert ids(app.rows) == {jobs["ml"], jobs["analyst"]}
        while app.current()["job_id"] != jobs["ml"]:
            await pilot.press("down")
        assert "strong ML fit" in str(app.query_one("#detail").render())
        await pilot.press("g")  # filter 1 (ml) was right
        await pilot.press("2", "b")  # filter 2 (research) was wrong
        fb = db.feedback_of(jobs["ml"])
        assert (fb["ml"]["correct"], fb["research"]["correct"]) == (True, False)
        await pilot.press("o")
        assert opened and "JR1" in opened[0]
        app.query_one("DataTable").move_cursor(row=[j["job_id"] for j in app.rows].index(jobs["ml"]))
        await pilot.press("a")
        assert db.job(jobs["ml"])["decision"] == "want_to_apply"
        assert jobs["ml"] in [j["job_id"] for j in app.rows]  # stays listed with ★ this session
        await pilot.press("u")
        assert db.job(jobs["ml"])["decision"] is None
        await pilot.press("slash", *"acme", "enter")
        assert [j["job_id"] for j in app.rows] == [jobs["analyst"]]
        await pilot.press("n", "r", *"no", "enter")
        assert db.job(jobs["analyst"])["decision"] == "not_applied" and db.reviews(jobs["analyst"])[0]["reason"] == "no"
        await pilot.press("escape", "tab")
        assert app.kind == "un_recommended" and [j["job_id"] for j in app.rows] == [jobs["sales"]]
        await pilot.press("tab")
        assert app.kind == "want_to_apply" and app.rows == []
        await pilot.press("q")


def test_cli_review_and_want_to_apply_state(env, tmp_path, monkeypatch):
    from auto_app.cli import app

    db, _, jobs, folder = env
    monkeypatch.chdir(tmp_path)
    r = CliRunner().invoke(app, ["job", "browse", "--db", str(db.db_path), "-f", "nope", "--dir", str(folder)])
    assert r.exit_code == 2 and "nope" in r.output
    r = CliRunner().invoke(app, ["job", "browse", "--db", str(db.db_path), "--dir", str(folder)])
    assert r.exit_code == 2 and "full-screen" in r.output  # not a terminal
    r = CliRunner().invoke(app, ["job", "mark", str(jobs["sales"]), "--as", "want_to_apply", "--db", str(db.db_path)])
    assert r.exit_code == 0, r.output
    assert db.job(jobs["sales"])["state"] == "want_to_apply" and db.job(jobs["sales"])["step2"] == "un_recommended"
    r = CliRunner().invoke(app, ["job", "list", "--state", "want_to_apply", "--db", str(db.db_path)])
    assert r.exit_code == 0 and "Sales Lead" in r.output and "queued" in r.output


async def test_theme_follows_the_system_or_stays_fixed(env):
    from auto_app.core.config import UiConfig
    from auto_app.process.job_browser import check_themes

    _, rv, _, _ = env
    mode = {"dark": False}
    app = JobBrowserApp(rv, dark=lambda: mode["dark"])
    async with app.run_test() as pilot:
        assert app.theme == "textual-light"
        mode["dark"] = True
        app.follow_system()  # (also runs every few seconds)
        assert app.theme == "textual-dark"
        app.theme = "nord"  # picked with ctrl+p: kept until the system's mode changes
        app.follow_system()
        assert app.theme == "nord"
        await pilot.press("q")
    fixed = JobBrowserApp(rv, ui=UiConfig(theme="solarized-light"), dark=lambda: True)
    async with fixed.run_test() as pilot:
        assert fixed.theme == "solarized-light"
        await pilot.press("q")
    assert check_themes(UiConfig()) == [] and check_themes(UiConfig(theme="nope", dark_theme="nord")) == ["nope"]


def test_system_dark_mode_detection(monkeypatch):
    import subprocess

    from auto_app.core import appearance

    monkeypatch.setattr(appearance.sys, "platform", "darwin")
    monkeypatch.setattr(appearance, "_run", lambda a: subprocess.CompletedProcess(a, 0, "Dark\n", ""))
    assert appearance.system_dark() is True
    monkeypatch.setattr(appearance, "_run", lambda a: subprocess.CompletedProcess(a, 1, "", "does not exist"))
    assert appearance.system_dark() is False  # light mode: the key is missing
    monkeypatch.setattr(appearance.sys, "platform", "linux")
    monkeypatch.setattr(appearance, "_run", lambda a: subprocess.CompletedProcess(a, 0, "'prefer-dark'\n", ""))
    assert appearance.system_dark() is True
    monkeypatch.setattr(appearance, "_run", lambda a: None)
    assert appearance.system_dark() is None
