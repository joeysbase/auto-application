"""C11: job state, job TTL (delete / archive), archived records, repost warnings, the old-status migration."""

from datetime import timedelta

import pytest
from sqlalchemy import select, update
from typer.testing import CliRunner

from auto_app.collect.store import PostingRef
from auto_app.core.config import AppConfig, JobTTL
from auto_app.core.db import tables as T
from auto_app.core.db.purge import purge
from auto_app.core.db.store import DbStore
from auto_app.core.models import utcnow
from auto_app.settings import Paths
from tests.process.test_link import OTHER, add, job_of, run, wd

TTL = JobTTL()  # 14 / 7 / 30 days


@pytest.fixture
def db(tmp_path):
    return DbStore(tmp_path / "data" / "auto_app.db", Paths(home=tmp_path))


def seen_days_ago(db, ref, days):
    """Make a posting look last seen `days` ago (and its job's dates follow on refresh)."""
    when = utcnow() - timedelta(days=days)
    with db.engine.begin() as conn:
        conn.execute(update(T.postings).where(T.postings.c.source == ref.split("/")[0],
                                              T.postings.c.source_job_id == ref.split("/")[1])
                     .values(last_seen_at=when))


def test_expiry_follows_the_latest_posting_and_the_state(db):
    add(db, "nvidia", "JR1", url=wd("JR1"))
    add(db, "linkedin", "11", apply_url=wd("JR1"))  # same job, seen on two sources
    run(db)
    j = job_of(db, "nvidia/JR1")
    seen_days_ago(db, "nvidia/JR1", 20)
    seen_days_ago(db, "linkedin/11", 3)  # still seen recently on LinkedIn
    db.refresh_job_dates(TTL)
    job = db.job(j)
    assert job["state"] == "not_evaluated"
    left = (utcnow() - timedelta(days=3) + timedelta(days=14)).date().isoformat()
    assert job["expires_at"][:10] == left  # 14 days after the *latest* sighting
    db.set_job_state(j, step2="recommended")
    db.refresh_job_dates(TTL)
    assert db.job(j)["expires_at"][:10] == (utcnow() - timedelta(days=3) + timedelta(days=30)).date().isoformat()


def test_unrecommended_and_unevaluated_jobs_are_deleted_with_their_postings(db):
    add(db, "nvidia", "JR2", url=wd("JR2"))
    add(db, "linkedin", "21", apply_url=wd("JR2"))
    add(db, "linkedin", "22", apply_url=wd("JR2") + "?trk=x")  # Level 1b duplicate of 21
    add(db, "nvidia", "JR3", url=wd("JR3"), title="Sales Lead", desc=OTHER)
    run(db)
    old, kept = job_of(db, "nvidia/JR2"), job_of(db, "nvidia/JR3")
    for ref in ("nvidia/JR2", "linkedin/21", "linkedin/22"):
        seen_days_ago(db, ref, 15)  # past the 14-day TTL of a not-evaluated job
    seen_days_ago(db, "nvidia/JR3", 6)
    db.set_job_state(kept, step2="un_recommended")  # 7-day TTL, seen 6 days ago: stays

    dry = purge(db, AppConfig().retention, ttl=TTL, dry_run=True)
    assert [e.job_id for e in dry.jobs.deleted] == [old] and dry.jobs.deleted_postings == 3
    assert db.job(old)  # a dry run changes nothing

    rep = purge(db, AppConfig().retention, ttl=TTL)
    assert [e.job_id for e in rep.jobs.deleted] == [old] and rep.jobs.archived == []
    assert db.job(old) is None and db.job(kept)
    for ref in ("nvidia/JR2", "linkedin/21", "linkedin/22"):
        assert not db.posting_exists(*ref.split("/"))  # gone: seen again later = collected as new
    with db.engine.connect() as conn:
        assert conn.execute(select(T.job_keys).where(T.job_keys.c.job_id == old)).all() == []
        assert conn.execute(select(T.job_locations).where(T.job_locations.c.job_id == old)).all() == []

    seen_days_ago(db, "nvidia/JR3", 8)
    assert [e.job_id for e in purge(db, AppConfig().retention, ttl=TTL).jobs.deleted] == [kept]


def test_recommended_jobs_are_archived_as_compact_records(db):
    add(db, "nvidia", "JR4", url=wd("JR4"), title="Applied Scientist")
    add(db, "linkedin", "41", apply_url=wd("JR4"), title="Applied Scientist")
    run(db)
    j = job_of(db, "nvidia/JR4")
    db.set_job_state(j, step2="recommended", decision="applied")
    for ref in ("nvidia/JR4", "linkedin/41"):
        seen_days_ago(db, ref, 31)
    rep = purge(db, AppConfig().retention, ttl=TTL)
    assert [e.job_id for e in rep.jobs.archived] == [j] and rep.jobs.deleted == []

    assert db.jobs() == []  # hidden from the active list
    [a] = db.jobs(archived=True)
    assert a["state"] == "archived" and a["decision"] == "applied" and a["title"] == "Applied Scientist"
    assert a["locations"] == ["santa clara, ca"] and set(a["sources"]) == {"nvidia", "linkedin"}
    assert [p["source_job_id"] for p in a["archive"]["postings"]] == ["JR4", "41"]
    assert db.archived_description(j).startswith("About the role")  # applied: the description is kept
    # posting identities stay (collect still skips the same listing), heavy data is gone
    assert db.posting_exists("linkedin", "41") and db.get_parsed(PostingRef("linkedin", "41")) is None
    assert db.get_posting(PostingRef("linkedin", "41"))["state"] == "archived"
    with db.engine.connect() as conn:
        assert conn.execute(select(T.posting_raw)).all() == []
        assert conn.execute(select(T.job_keys).where(T.job_keys.c.job_id == j)).all()  # keys kept for warnings

    # excluded from dedup: a new posting of the same employer job becomes a new job — with a warning
    add(db, "linkedin", "42", apply_url=wd("JR4") + "?trk=repost", title="Applied Scientist")
    plan, created = run(db)
    [new] = created.values()
    assert new != j and job_of(db, "linkedin/42") == new
    [w] = db.job(new)["warnings"]
    assert w["job_id"] == j and w["decision"] == "applied" and "which you applied to on" in w["text"]
    assert "employer_key" in w["keys"]
    db.refresh_job_dates(TTL)
    assert db.job(j)["state"] == "archived" and db.job(j)["expires_at"] is None  # archived jobs stay archived


def test_not_applied_jobs_drop_the_description_when_archived(db):
    add(db, "nvidia", "JR5", url=wd("JR5"))
    run(db)
    j = job_of(db, "nvidia/JR5")
    db.set_job_state(j, step2="recommended", decision="not_applied")
    seen_days_ago(db, "nvidia/JR5", 40)
    purge(db, AppConfig().retention, ttl=TTL)
    assert db.job(j)["state"] == "archived" and db.archived_description(j) == ""


def test_old_layout_retention_settings_still_load():
    cfg = AppConfig.model_validate({"retention": {"raw_days": 5, "description_days": 30,
                                                  "keep_description_statuses": ["applied"]}})
    assert cfg.retention.raw_days == 5 and cfg.jobs.ttl_days == JobTTL()


def test_migration_maps_old_statuses(tmp_path):
    from alembic import command
    from alembic.config import Config

    from auto_app.core.db.engine import MIGRATIONS, make_engine

    engine = make_engine(tmp_path / "old.db")
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS))
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "be25550e4963")  # the schema before C11
    with engine.begin() as conn:
        for i, status in enumerate(["collected", "filtered_out", "shortlisted", "skipped", "applied"], start=1):
            conn.exec_driver_sql(f"INSERT INTO jobs (job_id, status, purged, last_seen_at) "
                                 f"VALUES ({i}, '{status}', 0, '2026-09-30 00:00:00')")
            # postings and locations point at the job: the migration rebuilds `jobs` under them
            conn.exec_driver_sql(f"INSERT INTO postings (posting_id, source, source_job_id, kind, state, first_seen_at, "
                                 f"last_seen_at, job_id, purged) VALUES ({i}, 's', '{i}', 'api', 'linked', "
                                 f"'2026-09-30 00:00:00', '2026-09-30 00:00:00', {i}, 0)")
            conn.exec_driver_sql(f"UPDATE jobs SET primary_posting_id = {i} WHERE job_id = {i}")
            conn.exec_driver_sql(f"INSERT INTO job_locations (job_id, location_norm, posting_id) VALUES ({i}, 'x', {i})")
    store = DbStore(tmp_path / "old.db", Paths(home=tmp_path))  # opening upgrades to the latest schema
    states = {j["job_id"]: (j["step2"], j["decision"], j["state"]) for j in store.jobs()}
    assert states == {1: (None, None, "not_evaluated"), 2: ("un_recommended", None, "un_recommended"),
                      3: ("recommended", None, "recommended"), 4: ("recommended", "not_applied", "not_applied"),
                      5: ("recommended", "applied", "applied")}
    assert {j["job_id"]: j["postings"] for j in store.jobs()} == {i: 1 for i in range(1, 6)}  # links intact


def test_mark_command_sets_state_and_refuses_archived(db, tmp_path, monkeypatch):
    from auto_app.cli import app

    monkeypatch.setenv("AUTO_APP_HOME", str(tmp_path))
    add(db, "nvidia", "JR6", url=wd("JR6"))
    add(db, "nvidia", "JR7", url=wd("JR7"), title="Sales Lead", desc=OTHER)
    run(db)
    a, b = job_of(db, "nvidia/JR6"), job_of(db, "nvidia/JR7")
    r = CliRunner().invoke(app, ["mark", str(a), str(b), "--as", "applied"])
    assert r.exit_code == 0, r.output
    assert db.job(a)["state"] == "applied" and db.job(a)["step2"] == "recommended" and db.job(a)["expires_at"]
    assert CliRunner().invoke(app, ["mark", str(b), "--as", "not_evaluated"]).exit_code == 0
    assert db.job(b)["state"] == "not_evaluated"
    assert CliRunner().invoke(app, ["mark", str(a), "--as", "bogus"]).exit_code != 0
    seen_days_ago(db, "nvidia/JR6", 31)
    purge(db, AppConfig().retention, ttl=TTL)
    r = CliRunner().invoke(app, ["mark", str(a), "--as", "recommended"])
    assert r.exit_code == 1 and "archived" in r.output
    listing = CliRunner().invoke(app, ["jobs", "--archived"])
    assert listing.exit_code == 0 and "archived" in listing.output and "ML Engineer" in listing.output


def test_a_failing_migration_changes_nothing(tmp_path, monkeypatch):
    """All or nothing: an error part-way leaves the old schema exactly as it was."""
    import sqlite3

    from alembic import command
    from alembic.config import Config

    from auto_app.core.db import engine as E

    engine = E.make_engine(tmp_path / "old.db")
    cfg = Config()
    cfg.set_main_option("script_location", str(E.MIGRATIONS))
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "be25550e4963")
    real_upgrade = command.upgrade

    def failing(cfg, rev):
        real_upgrade(cfg, rev)
        raise RuntimeError("boom after the migration ran")

    monkeypatch.setattr(command, "upgrade", failing)
    with pytest.raises(RuntimeError, match="boom"):
        E.upgrade(engine)
    cols = [r[1] for r in sqlite3.connect(tmp_path / "old.db").execute("PRAGMA table_info(jobs)")]
    assert "status" in cols and "step2" not in cols  # rolled back completely
