"""The CLI the manager (Claude Code with skills) uses — docs/step4-run.md §5.3: --json, filters on `job list`,
`job mark --reason`, `filter label`, `job notified`."""

import json

import pytest
from typer.testing import CliRunner

from auto_app.cli import app
from tests.select.test_review import env  # noqa: F401 — env is a fixture


@pytest.fixture
def cli(env, monkeypatch):  # noqa: F811
    db, _, jobs, folder = env
    monkeypatch.setattr("auto_app.select.filters.FILTERS_DIR", folder)  # the test's filters, not yours

    def run(*args, ok=True):
        r = CliRunner().invoke(app, [*args, "--db", str(db.db_path)])
        assert (r.exit_code == 0) == ok, r.output
        return r.output
    return db, jobs, run


def test_job_list_json_and_filters(cli):
    db, jobs, run = cli
    rows = json.loads(run("job", "list", "--state", "recommended", "--json"))
    assert {r["job_id"] for r in rows} == {jobs["ml"], jobs["analyst"]}
    assert set(rows[0]) >= {"job_id", "title", "company", "locations", "apply_url", "step2_at", "notified_at"}
    assert [r["job_id"] for r in json.loads(run("job", "list", "--search", "acme", "--json"))] == [jobs["analyst"]]
    assert json.loads(run("job", "list", "--since", "1h", "--state", "recommended", "--json"))  # just judged
    assert json.loads(run("job", "list", "--since", "2099-01-01", "--json")) == []
    run("job", "list", "--since", "soon", ok=False)


def test_notified_leaves_the_digest(cli):
    db, jobs, run = cli
    assert "1 job(s) marked as notified" in run("job", "notified", str(jobs["ml"]))
    rows = json.loads(run("job", "list", "--state", "recommended", "--not-notified", "--json"))
    assert [r["job_id"] for r in rows] == [jobs["analyst"]]


def test_job_show_and_why_json(cli):
    db, jobs, run = cli
    j = json.loads(run("job", "show", str(jobs["ml"]), "--json", "-d"))
    assert j["title"] == "ML Engineer" and {v["filter"] for v in j["verdicts"]} == {"ml", "research"}
    assert j["description"] and j["postings"][0]["source"] == "nvidia"
    w = json.loads(run("filter", "why", str(jobs["ml"]), "--json"))
    by = {x["filter"]: x for x in w["judgments"]}
    assert by["ml"]["recommend"] is True and by["research"]["recommend"] is False and by["ml"]["reason"]
    assert by["ml"]["current_version"] is True and by["ml"]["your_label"] is None


def test_decide_with_a_reason_like_the_app(cli):
    db, jobs, run = cli
    run("job", "mark", str(jobs["analyst"]), "--as", "not_applied", "--reason", "it's sales, not engineering")
    assert db.job(jobs["analyst"])["decision"] == "not_applied"
    assert db.reviews(jobs["analyst"])[-1]["reason"] == "it's sales, not engineering"  # as the app's n + r
    run("job", "mark", str(jobs["ml"]), "--as", "applied", "--reason", "x", ok=False)  # a reason goes with a decision


def test_filter_label(cli):
    db, jobs, run = cli
    out = run("filter", "label", str(jobs["ml"]), "-f", "research", "--should", "--reason", "it is research")
    assert "research should recommend it" in out
    fb = db.feedback_of(jobs["ml"])["research"]
    assert fb["label"] is True and fb["reason"] == "it is research" and fb["origin"] == "explicit"
    assert "ml, research shouldn't recommend it" in run("filter", "label", str(jobs["sales"]), "--shouldnt",
                                                        "--reason", "sales")  # every filter: "no filter should"
    assert {f: v["label"] for f, v in db.feedback_of(jobs["sales"]).items()} == {"ml": False, "research": False}
    run("filter", "label", str(jobs["ml"]), "-f", "research", ok=False)  # --should or --shouldnt is required
    w = json.loads(run("filter", "why", str(jobs["ml"]), "--json"))
    assert {x["filter"]: x["your_label"] for x in w["judgments"]}["research"] == {"should_recommend": True,
                                                                                    "reason": "it is research"}


def test_more_json(cli, tmp_path, monkeypatch):
    db, jobs, run = cli
    assert isinstance(json.loads(run("source", "health", "--json")), dict)
    monkeypatch.setenv("AUTO_APP_HOME", str(tmp_path))
    st = json.loads(CliRunner().invoke(app, ["serve", "status", "--json"]).output)
    assert st["config"]["interval"] == "30m"


def test_unjudgeable_list(cli):
    from datetime import datetime

    from auto_app.select.engine import SelectReport
    from auto_app.select.health import assess

    db, jobs, run = cli
    assert json.loads(run("filter", "unjudgeable", "--json")) == []
    r = SelectReport()
    r.failures, r.succeeded = [(jobs["ml"], "ml", "page unreadable")], {"ml": 2}
    for _ in range(3):
        assess(r, datetime(2026, 10, 6, 12), store=db)
    [row] = json.loads(run("filter", "unjudgeable", "--json"))
    assert row["job_id"] == jobs["ml"] and row["filter"] == "ml" and row["tries"] == 3 and row["title"] == "ML Engineer"
