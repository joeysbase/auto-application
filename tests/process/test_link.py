"""C8c: dedup writing jobs — incremental linking, stable job ids/status, vetoes against existing jobs."""

import pytest
from typer.testing import CliRunner

from auto_app.collect.store import PostingRef
from auto_app.core.db.store import DbStore
from auto_app.core.models import CommonPosting
from auto_app.process.link import link_jobs
from auto_app.process.normalize import normalize_postings
from auto_app.settings import Paths
from auto_app.sources.api import RawJob
from tests.helpers import test_registry

BOARD = "https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite"
REG = test_registry()  # company source `nvidia` (tests/plugins)
DESC = "About the role\n" + " ".join(f"build{i} cuda kernels team{i % 9}" for i in range(150))
OTHER = "About the role\n" + " ".join(f"sell{i} accounts region{i % 4}" for i in range(150))


def wd(req):
    return f"{BOARD}/job/US-CA-Santa-Clara/Engineer_{req}"


@pytest.fixture
def db(tmp_path):
    return DbStore(tmp_path / "data" / "auto_app.db", Paths(home=tmp_path))


def add(db, source, jid, *, title="ML Engineer", company="NVIDIA", url=None, apply_url=None, desc=DESC,
        locations=("Santa Clara, CA",), employment="full_time", req=None):
    db.save_api_posting(source, RawJob(id=jid, url=url or f"https://x/{jid}", title=title, record={}), None,
                        platform="t", board=None, batch_id="b", search_run_id=1)
    db.save_parsed(PostingRef(source, jid), CommonPosting(
        source=source, source_job_id=jid, title=title, company=company, url=url, apply_url=apply_url or url,
        description_text=desc, locations=list(locations), employment_type=employment, req_id=req), "t:1")


def run(db, dry=False):
    normalize_postings(db, REG)
    return link_jobs(db, REG, dry_run=dry)


def job_of(db, ref):
    return db.get_posting(PostingRef(*ref.split("/")))["job_id"]


def test_first_run_creates_jobs_and_second_run_changes_nothing(db):
    add(db, "nvidia", "JR1", url=wd("JR1"))
    add(db, "linkedin", "11", apply_url=wd("JR1") + "?source=LinkedIn", locations=("Austin, TX",))
    add(db, "linkedin", "12", title="Account Executive", desc=OTHER)
    plan, created = run(db)
    assert len(created) == 2
    j = job_of(db, "nvidia/JR1")
    assert job_of(db, "linkedin/11") == j != job_of(db, "linkedin/12")
    job = db.job(j)
    assert job["primary_source"] == "nvidia" and job["postings"] == 2  # company posting is primary
    assert job["locations"] == ["austin, tx", "santa clara, ca"]
    assert ("employer_key", "workday:nvidia/nvidiaexternalcareersite:JR1") in db.job_keys_of(j)
    assert db.get_posting(PostingRef("linkedin", "11"))["state"] == "linked"

    again, created2 = run(db)
    assert again.new_postings == 0 and created2 == {}
    assert job_of(db, "linkedin/11") == j  # ids stable


def test_new_posting_joins_existing_job_and_company_posting_becomes_primary(db):
    add(db, "linkedin", "21", apply_url=wd("JR2"))
    run(db)
    j = job_of(db, "linkedin/21")
    assert db.job(j)["primary_source"] == "linkedin"
    add(db, "nvidia", "JR2", url=wd("JR2"))
    plan, created = run(db)
    assert created == {} and plan.joined == {j: ["nvidia/JR2"]}
    assert job_of(db, "nvidia/JR2") == j
    assert db.job(j)["primary_source"] == "nvidia" and db.job(j)["postings"] == 2


def test_state_survives_a_repost(db):
    add(db, "linkedin", "31", apply_url=wd("JR3"))
    run(db)
    j = job_of(db, "linkedin/31")
    db.set_job_state(j, step2="recommended", decision="applied")
    add(db, "linkedin", "32", apply_url=wd("JR3") + "?trk=repost")  # same site, new id, same employer job
    plan, _ = run(db)
    p = db.get_posting(PostingRef("linkedin", "32"))
    assert p["job_id"] == j and p["duplicate_of"] == "linkedin/31" and p["match_rule"] == "1b:employer_key"
    assert db.job(j)["state"] == "applied"


def test_veto_against_existing_job_creates_a_new_job(db):
    add(db, "nvidia", "JR4", url=wd("JR4"))
    run(db)
    # same company, same text and title, but an internship: must not join the full-time job
    add(db, "linkedin", "41", employment="internship", apply_url="https://elsewhere.example/apply")
    plan, created = run(db)
    assert len(created) == 1 and job_of(db, "linkedin/41") != job_of(db, "nvidia/JR4")
    assert not plan.joined


def test_preview_writes_nothing_and_rebuild_regroups(db):
    add(db, "nvidia", "JR5", url=wd("JR5"))
    add(db, "linkedin", "51", apply_url=wd("JR5"))
    plan, created = run(db, dry=True)
    assert created == {} and len(plan.new_jobs) == 1 and db.jobs() == []
    assert db.get_posting(PostingRef("linkedin", "51"))["state"] == "normalized"
    run(db)
    assert len(db.jobs()) == 1
    assert db.clear_jobs() == 1 and db.jobs() == []
    assert db.get_posting(PostingRef("linkedin", "51"))["state"] == "normalized"
    run(db)
    assert len(db.jobs()) == 1


def test_cli_refuses_rebuild_once_a_job_moved_on(tmp_path, monkeypatch):
    from auto_app.cli import app

    monkeypatch.setenv("AUTO_APP_HOME", str(tmp_path))
    db = DbStore(tmp_path / "data" / "auto_app.db", Paths(home=tmp_path))
    add(db, "linkedin", "61", apply_url=wd("JR6"))
    run(db)
    db.set_job_state(db.jobs()[0]["job_id"], step2="recommended")
    result = CliRunner().invoke(app, ["process", "dedup", "--rebuild"])
    assert result.exit_code == 2 and "refusing to rebuild" in result.output
    assert len(db.jobs()) == 1


def test_match_to_one_member_but_veto_by_another_is_blocked(db):
    # existing job: NVIDIA full-time posting + a LinkedIn copy (employment unknown, req R9)
    add(db, "nvidia", "JR7", url=wd("JR7"), req="R7")
    add(db, "linkedin", "71", apply_url=wd("JR7"), employment="unknown", req="R9")
    run(db)
    j = job_of(db, "nvidia/JR7")
    assert job_of(db, "linkedin/71") == j
    # new Handshake internship: same company + req as the LinkedIn copy (a match), but vetoed by the full-time posting
    add(db, "handshake", "h1", employment="internship", req="R9", apply_url="https://elsewhere.example/a")
    plan, created = run(db)
    assert job_of(db, "handshake/h1") != j and len(created) == 1
    assert any("blocked" in why and "nvidia/JR7" in why for _, _, why in plan.near_misses)
