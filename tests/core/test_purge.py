"""Retention: what purge removes, what it keeps, and that later stages still work afterwards."""

import json
import os
import time
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import update

from auto_app.collect.store import PostingRef, RawPurged
from auto_app.core.config import AppConfig
from auto_app.core.db import tables as T
from auto_app.core.db.purge import purge
from auto_app.core.db.store import DbStore
from auto_app.core.models import utcnow
from auto_app.process.dedup import load_items
from auto_app.process.link import link_jobs
from auto_app.process.normalize import normalize_postings
from auto_app.process.parse import parse_postings
from auto_app.settings import Paths
from auto_app.sources.api import RawJob
from auto_app.platforms import PLATFORMS
from tests.helpers import test_registry

FIX = Path(__file__).parents[1] / "fixtures"
CFG = AppConfig()
REG = test_registry()  # company source `uipath` (tests/plugins)


@pytest.fixture
def db(tmp_path):
    db = DbStore(tmp_path / "data" / "auto_app.db", Paths(home=tmp_path))
    plugin = PLATFORMS["ashby"]()
    jobs = json.loads((FIX / "ashby/uipath.json").read_text())["jobs"][:2]
    for j in jobs:
        db.save_api_posting("uipath", plugin.to_job(j), None, platform="ashby", board="uipath", batch_id="b", search_run_id=1)
    # a posting that never parses (no plugin): keeps its raw data
    db.save_api_posting("nosuch", RawJob(id="x1", url="https://x", title="t", record={"a": 1}), None,
                        platform="none", board=None, batch_id="b", search_run_id=1)
    parse_postings(db, CFG, REG, sources=["uipath"])
    normalize_postings(db, REG)
    link_jobs(db, REG)
    shot = tmp_path / "data" / "screenshots" / "shot.png"
    shot.parent.mkdir(parents=True)
    shot.write_bytes(b"png")
    old = utcnow() - timedelta(days=60)
    with db.engine.begin() as conn:
        conn.execute(update(T.posting_raw).values(fetched_at=old))
        a = conn.execute(T.postings.select().where(T.postings.c.source_job_id == jobs[0]["id"])).mappings().first()
        conn.execute(update(T.posting_raw).where(T.posting_raw.c.posting_id == a["posting_id"]).values(screenshot_path=str(shot)))
        b = conn.execute(T.postings.select().where(T.postings.c.source_job_id == jobs[1]["id"])).mappings().first()
        conn.execute(update(T.jobs).where(T.jobs.c.job_id == b["job_id"]).values(step2="recommended", decision="applied"))
    db.write_batch("b", {"batch_id": "b", "started_at": old, "sources": {}})
    run = db.start_search_run("b", {"source": "uipath", "search_name": "uipath", "started_at": old})
    db.add_sighting("b", {"search_run_id": run, "source": "uipath", "source_job_id": "z", "outcome": "known",
                          "seen_at": old.isoformat()}, None)
    log = db.paths.logs / "old.log"
    log.parent.mkdir(parents=True)
    log.write_text("x")
    os.utime(log, (time.time() - 90 * 86400,) * 2)
    db.test_refs = (PostingRef("uipath", jobs[0]["id"]), PostingRef("uipath", jobs[1]["id"]), shot, log)
    return db


def test_dry_run_changes_nothing(db):
    a, b, shot, log = db.test_refs
    rep = purge(db, CFG.retention, dry_run=True)
    assert (rep.raw_postings, rep.screenshots, rep.sightings, rep.log_files) == (2, 1, 1, 1)
    assert db.load_raw(a).record and shot.exists() and log.exists()


def test_purge_removes_heavy_data_but_keeps_identity_and_jobs(db):
    a, b, shot, log = db.test_refs
    rep = purge(db, CFG.retention)  # no job TTL: posting-data retention only
    assert rep.raw_postings == 2 and rep.sightings == 1
    with pytest.raises(RawPurged):
        db.load_raw(a)
    assert db.load_raw(PostingRef("nosuch", "x1")).record == {"a": 1}  # never parsed → raw kept for a fix
    assert not shot.exists() and not log.exists()
    assert db.get_parsed(a)["description_text"] and db.get_parsed(b)["description_text"]  # parsed fields stay
    assert db.posting_exists(*a) and db.get_normalized(a)["minhash"]  # identity and keys stay
    assert len(db.jobs()) == 2 and {j["state"] for j in db.jobs()} == {"not_evaluated", "applied"}
    assert db.get_sightings("b") == []


def test_stages_still_work_after_purge(db):
    a, *_ = db.test_refs
    purge(db, CFG.retention)
    st = parse_postings(db, CFG, REG, reparse=True)["uipath"]
    assert st.failed == 0 and st.raw_purged == 2  # earlier parses kept, not marked failed
    before = db.get_normalized(a)["minhash"]
    normalize_postings(db, REG, again=True)
    assert db.get_normalized(a)["minhash"] == before  # description signature survives re-normalizing
    items = load_items(db, REG)
    assert items[str(a)].shingle_count > 0 and items[str(a)].shingle_set  # raw purged; parsed text and keys remain
    plan, created = link_jobs(db, REG)
    assert plan.new_postings == 0 and created == {}
