"""S2: `select` end to end on a database — which jobs are judged, Step 2 state, re-runs, budget, reparse, `why`."""

import shutil

import pytest
from typer.testing import CliRunner

from auto_app.core.config import AppConfig
from auto_app.core.db.store import DbStore
from auto_app.select.engine import SelectOptions, reparse, run_select
from auto_app.select.filters import EXAMPLES_DIR, load_filter
from auto_app.select.runner import api_runner
from auto_app.settings import Paths
from tests.process.test_link import OTHER, add, job_of, run, wd
from tests.select.fake_model import FakeClient, answer, prompt_of, response, text

CFG = AppConfig.model_validate({"select": {"prices": {"claude-sonnet-5": {"input_per_mtok": 3, "output_per_mtok": 15}}}})


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
    jobs = {t: job_of(db, ref) for t, ref in (("ml", "nvidia/JR1"), ("sales", "nvidia/JR2"), ("analyst", "linkedin/L3"))}
    return db, folder, jobs


def by_title(kwargs):
    p = prompt_of(kwargs)
    if "Title: ML Engineer" in p:
        return answer(True, "strong ML fit")
    if "Title: Sales Lead" in p:
        return answer(False, "not engineering")
    return response(text("Not sure, it's an analyst role."))  # no JSON → couldn't read


def test_select_judges_sets_state_and_does_not_repeat(env):
    db, folder, jobs = env
    flt = load_filter(folder / "ml")
    client = FakeClient(by_title)
    rep = run_select(db, CFG, [flt], api_runner(client), SelectOptions(parallel=2))
    assert rep.judged == 3 and (rep.recommend, rep.no, rep.unparsed, rep.errors) == (1, 1, 1, 0)
    assert db.job(jobs["ml"])["step2"] == "recommended"
    assert db.job(jobs["sales"])["step2"] == "un_recommended"
    assert db.job(jobs["analyst"])["step2"] == "recommended"  # couldn't read → shown to you (high recall)
    assert rep.cost == pytest.approx(3 * (100 * 3 + 20 * 15) / 1e6)
    sent = [prompt_of(c) for c in client.calls]
    assert any("Title: ML Engineer" in p and "<job_posting>" in p and "$ARGUMENTS" not in p for p in sent)
    [row] = db.judgment_rows(job_id=jobs["ml"], with_answer=True)
    assert row["status"] == "ok" and row["reason"] == "strong ML fit" and row["filter_version"] == flt.version
    assert row["trace"][0]["blocks"][0]["type"] == "text"
    assert run_select(db, CFG, [flt], api_runner(FakeClient(by_title)), SelectOptions()).planned == []  # nothing new


def test_new_versions_need_rescore_and_decided_jobs_are_never_touched(env):
    db, folder, jobs = env
    run_select(db, CFG, [load_filter(folder / "ml")], api_runner(FakeClient(by_title)), SelectOptions())
    db.set_job_state(jobs["ml"], step2="recommended", decision="applied")
    (folder / "ml" / "command.md").write_text((folder / "ml" / "command.md").read_text() + "\nBe strict.\n")
    v2 = load_filter(folder / "ml")
    assert run_select(db, CFG, [v2], api_runner(FakeClient(by_title)), SelectOptions()).planned == []
    flipped = FakeClient(lambda kw: answer(True, "v2 likes everything"))
    rep = run_select(db, CFG, [v2], api_runner(flipped), SelectOptions(rescore=True))
    assert sorted(j for j, _ in rep.planned) == sorted([jobs["sales"], jobs["analyst"]])  # not the applied job
    assert db.job(jobs["sales"])["step2"] == "recommended" and db.job(jobs["ml"])["decision"] == "applied"


def test_failed_runs_stay_undecided_and_are_retried(env):
    db, folder, jobs = env
    flt = load_filter(folder / "ml")

    def flaky(kwargs):
        if "Sales Lead" in prompt_of(kwargs):
            raise TimeoutError("API timeout")
        return by_title(kwargs)

    rep = run_select(db, CFG, [flt], api_runner(FakeClient(flaky)), SelectOptions())
    assert rep.errors == 1 and db.job(jobs["sales"])["step2"] is None
    rep = run_select(db, CFG, [flt], api_runner(FakeClient(by_title)), SelectOptions())
    assert [j for j, _ in rep.planned] == [jobs["sales"]] and db.job(jobs["sales"])["step2"] == "un_recommended"


def test_several_filters_or_and_applies_to(env):
    db, folder, jobs = env
    shutil.copytree(folder / "ml", folder / "sales")
    (folder / "sales" / "filter.yaml").write_text("model: claude-sonnet-5\napplies_to: [nvidia]\n")
    ml, sales = load_filter(folder / "ml"), load_filter(folder / "sales")

    def script(kwargs):
        p = prompt_of(kwargs)
        if "Sales Lead" in p and "Be a sales filter" in p:
            return answer(True, "sales filter likes it")
        return by_title(kwargs)

    (folder / "sales" / "command.md").write_text((folder / "sales" / "command.md").read_text() + "\nBe a sales filter.\n")
    sales = load_filter(folder / "sales")
    rep = run_select(db, CFG, [ml, sales], api_runner(FakeClient(script)), SelectOptions())
    assert sorted(n for j, n in rep.planned if j == jobs["analyst"]) == ["ml"]  # linkedin job: not in sales' applies_to
    assert db.job(jobs["sales"])["step2"] == "recommended"  # ml said no, sales said yes → OR
    assert db.job(jobs["ml"])["step2"] == "recommended"


def test_budget_and_limit(env):
    db, folder, jobs = env
    flt = load_filter(folder / "ml")
    assert len({j for j, _ in run_select(db, CFG, [flt], None, SelectOptions(limit=2, dry_run=True)).planned}) == 2
    rep = run_select(db, CFG, [flt], api_runner(FakeClient(by_title)), SelectOptions(max_cost=0.0001, parallel=1))
    assert rep.judged == 1 and rep.skipped_budget == 2  # one run spent more than the budget; the rest weren't started


def test_reparse_reads_stored_answers_again_without_runs(env):
    db, folder, jobs = env
    run_select(db, CFG, [load_filter(folder / "ml")], api_runner(FakeClient(by_title)), SelectOptions())
    (folder / "ml" / "parser.py").write_text(
        "def parse(answer):\n"
        "    if 'analyst' in answer:\n"
        "        return {'recommend': False, 'reason': 'analyst role'}\n"
        "    import json\n    return json.loads(answer[answer.rindex('{'):])\n")
    flt = load_filter(folder / "ml")
    changes = reparse(db, CFG, [flt], dry_run=True)
    assert len(changes) == 1 and "couldn't read → no" in changes[0] and db.job(jobs["analyst"])["step2"] == "recommended"
    reparse(db, CFG, [flt])
    assert db.job(jobs["analyst"])["step2"] == "un_recommended"
    [row] = db.judgment_rows(job_id=jobs["analyst"])
    assert row["status"] == "ok" and row["parser"].startswith("parser.py:")


def test_cli_select_and_why(env, tmp_path, monkeypatch):
    from auto_app import cli

    db, folder, jobs = env
    monkeypatch.setenv("AUTO_APP_HOME", str(tmp_path))
    r = CliRunner().invoke(cli.app, ["filter", "run", "--dir", str(folder), "--dry-run"])
    assert r.exit_code == 0 and "would run 3 judgments on 3 jobs" in r.output
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("AUTO_APP_SELECT__CLAUDE_CLI", "no-such-claude")  # never start a real CLI in tests
    r = CliRunner().invoke(cli.app, ["filter", "run", "--dir", str(folder)])
    assert r.exit_code == 2 and "no ANTHROPIC_API_KEY and no `no-such-claude` CLI found" in r.output
    monkeypatch.setenv("AUTO_APP_SELECT__RUNNER", "api")
    r = CliRunner().invoke(cli.app, ["filter", "run", "--dir", str(folder)])
    assert r.exit_code == 2 and "ANTHROPIC_API_KEY is not set" in r.output
    r = CliRunner().invoke(cli.app, ["filter", "run", "--dir", str(folder), "--max-cost", "1"])
    assert r.exit_code == 2 and "needs a price" in r.output  # claude-sonnet-5 isn't priced by default (api runner)
    monkeypatch.delenv("AUTO_APP_SELECT__RUNNER")
    run_select(db, CFG, [load_filter(folder / "ml")], api_runner(FakeClient(by_title)), SelectOptions())
    r = CliRunner().invoke(cli.app, ["filter", "why", str(jobs["analyst"]), "--dir", str(folder)])
    assert r.exit_code == 0 and "couldn't read" in r.output and "(current)" in r.output and "analyst role" in r.output


def test_job_option_judges_exactly_those_jobs_again(env):
    db, folder, jobs = env
    flt = load_filter(folder / "ml")
    run_select(db, CFG, [flt], api_runner(FakeClient(by_title)), SelectOptions())
    db.set_job_state(jobs["sales"], step2="recommended", decision="not_applied")
    client = FakeClient(lambda kw: answer(True, "second look"))
    rep = run_select(db, CFG, [flt], api_runner(client), SelectOptions(jobs=[jobs["ml"], jobs["sales"]]))
    assert sorted(j for j, _ in rep.planned) == sorted([jobs["ml"], jobs["sales"]])  # already judged, run again
    assert len(client.calls) == 2 and jobs["analyst"] not in {j for j, _ in rep.planned}
    [row] = db.judgment_rows(job_id=jobs["ml"])
    assert row["reason"] == "second look"  # replaced the earlier judgment of the same version
    assert db.job(jobs["sales"])["decision"] == "not_applied"  # judged, but your decision stands


def test_stream_shows_each_run_live_one_at_a_time(env):
    db, folder, jobs = env
    flt = load_filter(folder / "ml")
    out = []
    client = FakeClient(by_title)
    rep = run_select(db, CFG, [flt], api_runner(client), SelectOptions(stream=True, parallel=3), write=out.append)
    shown = "".join(out)
    assert rep.judged == 3 and client.streamed == 3
    assert shown.count("── [") == 3 and "── [3/3] #" in shown and "· ml (claude-sonnet-5)" in shown
    assert "strong ML fit" in shown and "analyst role" in shown  # the agents' text, live
    # one at a time: each header is followed by that job's own text before the next header
    blocks = shown.split("── [")[1:]
    assert len(blocks) == 3
    assert all(("ML Engineer" in b) == ("strong ML fit" in b) for b in blocks)


def test_progress_is_reported_per_run_with_counts_and_cost(env):
    import io

    from rich.console import Console

    from auto_app.select.engine import Progress
    from auto_app.select.progress_view import LineProgress, LiveProgress, make_progress

    db, folder, jobs = env
    events = []

    class Sink(Progress):
        def start(self, total, jobs, parallel):
            events.append(("start", total, jobs, parallel))

        def began(self, job_id, flt):
            events.append(("began", job_id))

        def done(self, job_id, flt, line, n, total, spent):
            events.append(("done", n, total, line.split()[0], spent))

        def finish(self):
            events.append(("finish",))

    run_select(db, CFG, [load_filter(folder / "ml")], api_runner(FakeClient(by_title)), SelectOptions(parallel=2),
               progress=Sink())
    assert events[0] == ("start", 3, 3, 2) and events[-1] == ("finish",)
    done = [e for e in events if e[0] == "done"]
    assert [e[1] for e in done] == [1, 2, 3] and all(e[2] == 3 for e in done)
    assert sorted(e[3] for e in done) == sorted(f"#{j}" for j in jobs.values())
    assert done[-1][4] == pytest.approx(3 * (100 * 3 + 20 * 15) / 1e6)  # cost so far
    assert len([e for e in events if e[0] == "began"]) == 3

    out = []
    lp = LineProgress(out.append)
    lp.start(12, 10, 3)
    lp.done(1, None, "#1     ml   recommend", 2, 12, 0.5)
    lp.finish()
    assert out[0] == "judging 12 runs on 10 jobs, 3 at a time" and out[1] == "[ 2/12] #1     ml   recommend"
    assert out[2].startswith("took ")

    buf = io.StringIO()
    console = Console(file=buf, force_terminal=True, width=150)
    assert isinstance(make_progress(False, print, console), LiveProgress)
    assert isinstance(make_progress(True, print, console), LineProgress)  # --stream: no bar
    assert isinstance(make_progress(False, print, Console(file=io.StringIO())), LineProgress)  # not a terminal
    live = LiveProgress(console)
    flt = load_filter(folder / "ml")
    live.start(2, 2, 2)
    live.began(5, flt)
    assert "running #5" in live._running_text()
    live.done(5, flt, "#5     ml   no — [not] markup", 1, 2, 0.01)
    live.finish()
    text = buf.getvalue()
    assert "[1/2] #5" in text and "[not] markup" in text and "took " in text
