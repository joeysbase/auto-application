"""`auto-app run` pipeline end to end on the fake job site, the run lock, and the launchd schedule."""

import pytest

from auto_app.collect.plan import Selection
from auto_app.collect.runner import CollectOptions
from auto_app.core.config import AppConfig, Defaults, SourceConfig, SourceSearch
from auto_app.core.db.store import DbStore
from auto_app.pipeline import RunLocked, run_lock, run_pipeline
from auto_app.schedule import build_plist, parse_times
from auto_app.settings import Paths
from auto_app.sources.registry import PluginInfo, load_registry
from tests.fakesite.site import FakeAdapter, FakeSite


async def test_run_collects_parses_normalizes_and_links(tmp_path):
    site = FakeSite()
    cfg = AppConfig(defaults=Defaults(early_stop_known_streak=2),
                    sources={"fake": SourceConfig(searches=[SourceSearch(name="ml", keywords=["ml"])])})
    reg = load_registry()
    reg.add(PluginInfo("fake", "browser", FakeAdapter, "test"))
    paths = Paths(home=tmp_path)
    db = DbStore(paths.db, paths)
    opts = CollectOptions(headless=True, fast=True, context_hook=site.install)
    res = await run_pipeline(cfg, paths, db, opts, Selection(), reg, do_purge=True)
    assert res.batch["sources"]["fake"]["outcomes"]["fetched"] == 5  # 101–105; 404 failed
    assert sum(st.parsed for st in res.parse.values()) == 5  # default JSON-LD parser
    assert res.new_jobs == 5 and len(db.jobs()) == 5
    assert res.purge is not None and res.purge.raw_postings == 0  # nothing old enough yet
    assert res.problems == []  # one job page 404s, but the source itself finished ok

    again = await run_pipeline(cfg, paths, db, opts, Selection(), reg)
    assert again.new_jobs == 0 and len(db.jobs()) == 5


def test_run_lock_prevents_overlap(tmp_path):
    with run_lock(tmp_path / "run.lock"):
        with pytest.raises(RunLocked, match="in progress"):
            with run_lock(tmp_path / "run.lock"):
                pass
    with run_lock(tmp_path / "run.lock"):  # released
        pass


def test_schedule_plist():
    p = build_plist(["09:00", "17:30"], headless=False, uv="/opt/homebrew/bin/uv")
    assert p["ProgramArguments"][:2] == ["/opt/homebrew/bin/uv", "run"]
    assert p["ProgramArguments"][-3:] == ["run", "--scheduled", "--purge"]
    assert p["StartCalendarInterval"] == [{"Hour": 9, "Minute": 0}, {"Hour": 17, "Minute": 30}]
    assert "--headless" in build_plist(["9"], headless=True)["ProgramArguments"]
    with pytest.raises(ValueError):
        parse_times(["25:00"])
