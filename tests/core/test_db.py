"""Database store: schema, import from files, export to files."""

import json
from pathlib import Path

from sqlalchemy import inspect

from auto_app.collect.store import FileStore
from auto_app.core.config import AppConfig
from auto_app.core.db.importer import export_posting, import_file_store
from auto_app.core.db.store import DbStore
from auto_app.core.models import utcnow
from auto_app.process.normalize import normalize_postings
from auto_app.process.parse import parse_postings
from auto_app.settings import Paths
from auto_app.platforms import PLATFORMS
from tests.helpers import test_registry

FIX = Path(__file__).parents[1] / "fixtures"
CFG = AppConfig()
REG = test_registry()  # company source `uipath` (tests/plugins)


def seeded_files(root: Path) -> FileStore:
    """A small file store: one batch with a run and sightings, two parsed + normalized postings."""
    fs = FileStore(root)
    now = utcnow()
    fs.write_batch("b1", {"batch_id": "b1", "started_at": now, "finished_at": now, "sources": {}, "options": {}})
    run = fs.start_search_run("b1", {"batch_id": "b1", "search_name": "uipath", "source": "uipath", "started_at": now})
    fs.finish_search_run("b1", run, {"source": "uipath", "finished_at": now, "stop_reason": "done", "outcomes": {"fetched": 2}})
    plugin = PLATFORMS["ashby"]()
    for j in json.loads((FIX / "ashby/uipath.json").read_text())["jobs"][:2]:
        job = plugin.to_job(j)
        fs.save_api_posting("uipath", job, None, platform="ashby", board="uipath", batch_id="b1", search_run_id=run)
        fs.add_sighting("b1", {"search_run_id": run, "batch_id": "b1", "search_name": "uipath", "source": "uipath",
                               "source_job_id": job.id, "title": job.title, "outcome": "fetched", "seen_at": now.isoformat()},
                        None)
    fs.update_health("uipath", status="ok", last_ok_at=now)
    parse_postings(fs, CFG, REG)
    normalize_postings(fs, REG)
    return fs


def test_schema_is_created_by_migrations_and_reopening_is_safe(tmp_path):
    db = DbStore(tmp_path / "a.db", Paths(home=tmp_path))
    names = set(inspect(db.engine).get_table_names())
    assert {"postings", "posting_raw", "batches", "search_runs", "search_results", "source_health",
            "jobs", "job_locations", "job_keys", "company_aliases", "alembic_version"} <= names
    DbStore(tmp_path / "a.db", Paths(home=tmp_path))  # upgrade again: no-op


def test_import_files_is_complete_and_idempotent(tmp_path):
    fs = seeded_files(tmp_path / "raw")
    db = DbStore(tmp_path / "data" / "auto_app.db", Paths(home=tmp_path))
    report = import_file_store(fs, db)
    assert "batches   1 new" in report and "postings  2 new" in report
    for ref in fs.iter_postings():
        assert db.get_posting(ref)["state"] == fs.get_posting(ref)["state"] == "normalized"
        assert db.get_parsed(ref) == fs.get_parsed(ref)
        assert db.get_normalized(ref) == fs.get_normalized(ref)
        assert db.load_raw(ref).record == fs.load_raw(ref).record
    [run] = db.get_search_runs("b1")
    assert run["stop_reason"] == "done" and run["outcomes"] == {"fetched": 2}
    assert {s["search_run_id"] for s in db.get_sightings("b1")} == {run["search_run_id"]}  # run ids remapped
    assert db.source_health("uipath")["status"] == "ok"

    again = import_file_store(fs, db)
    assert "batches   0 new, 1 already there" in again and "postings  0 new, 2 already there" in again


def test_export_round_trips_to_files(tmp_path):
    fs = seeded_files(tmp_path / "raw")
    db = DbStore(tmp_path / "db.sqlite", Paths(home=tmp_path))
    import_file_store(fs, db)
    ref = next(db.iter_postings())
    out = FileStore(tmp_path / "export")
    export_posting(db, ref, out)
    assert out.get_parsed(ref) == db.get_parsed(ref)
    assert out.get_normalized(ref) == db.get_normalized(ref)
    assert out.load_raw(ref).record == db.load_raw(ref).record
