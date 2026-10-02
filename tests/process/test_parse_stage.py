"""The parse stage over a store: states, re-parse rules, failures, coverage."""

import json
from pathlib import Path

from auto_app.collect.store import PostingRef
from auto_app.core.config import AppConfig
from auto_app.process.parse import format_coverage, parse_postings
from auto_app.sources.api import RawJob
from auto_app.platforms import PLATFORMS
from tests.helpers import test_registry

FIX = Path(__file__).parents[1] / "fixtures"
CFG = AppConfig()
REG = test_registry()  # company source `uipath` (tests/plugins)


def seed(store):
    plugin = PLATFORMS["ashby"]()
    for j in json.loads((FIX / "ashby/uipath.json").read_text())["jobs"][:2]:
        store.save_api_posting("uipath", plugin.to_job(j), None, platform="ashby", board="uipath", batch_id="b", search_run_id=1)
    broken = RawJob(id="broken", url="https://x", title="", record={"id": "broken"})
    store.save_api_posting("uipath", broken, None, platform="ashby", board="uipath", batch_id="b", search_run_id=1)


def test_parse_states_failures_and_reparse(store):
    seed(store)
    stats = parse_postings(store, CFG, REG)["uipath"]
    assert (stats.parsed, stats.failed) == (2, 1)
    states = {r.job_id: store.get_posting(r)["state"] for r in store.iter_postings("uipath")}
    assert sorted(states.values()) == ["parse_failed", "parsed", "parsed"]
    assert store.get_posting(PostingRef("uipath", "broken"))["parse_error"] == "no title found"
    parsed = store.get_parsed(next(r for r in store.iter_postings("uipath") if states[r.job_id] == "parsed"))
    assert parsed["company"] == "UiPath"

    again = parse_postings(store, CFG, REG)["uipath"]
    assert (again.parsed, again.skipped, again.failed) == (0, 2, 1)  # parsed ones skipped; failed retried
    forced = parse_postings(store, CFG, REG, reparse=True)["uipath"]
    assert forced.parsed == 2

    table = format_coverage({"uipath": again})
    assert "uipath" in table and "100%" in table and "no title found" in table
