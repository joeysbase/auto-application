"""Company collection end to end over a fake HTTP transport (no network)."""

import json
from pathlib import Path

import httpx

from auto_app.collect.plan import Selection
from auto_app.collect.report import format_one, format_table
from auto_app.collect.runner import CollectionRunner, CollectOptions
from auto_app.collect.store import PostingRef  # noqa: F401
from auto_app.core.config import AppConfig
from auto_app.settings import Paths
from tests.helpers import test_registry

FIX = Path(__file__).parents[1] / "fixtures"


def fixture(rel):
    return json.loads((FIX / rel).read_text())


class Site:
    def __init__(self):
        self.status = 200
        self.requests: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.requests.append(f"{request.method} {url}")
        if self.status != 200:
            return httpx.Response(self.status)
        if "ashbyhq.com" in url:
            return httpx.Response(200, json=fixture("ashby/uipath.json"))
        if "myworkdayjobs.com" in url and url.endswith("/jobs"):
            body = json.loads(request.content)
            return httpx.Response(200, json=fixture("workday/nvidia_facets.json" if body["limit"] == 1 else "workday/nvidia_search.json"))
        if "myworkdayjobs.com" in url:
            return httpx.Response(200, json=fixture("workday/nvidia_detail.json"))
        return httpx.Response(404)


CONFIG = {
    "sources": {
        "uipath": {"searches": [{"name": "applied", "keywords": ["applied scientist"], "locations": ["United States"],
                                 "posted_within_days": 3650}]},
        "nvidia": {"searches": [{"name": "mle", "keywords": ["machine learning engineer"], "locations": ["United States"],
                                 "posted_within_days": 1}]},
    },
}


def make(tmp_path, site, store, **opts):
    cfg = AppConfig.model_validate(CONFIG)
    runner = CollectionRunner(cfg, Paths(home=tmp_path), store, CollectOptions(fast=True, **opts),
                              registry=test_registry(), http_transport=httpx.MockTransport(site.handler))
    return store, runner


async def test_companies_collected_and_known_next_time(tmp_path, store):
    site = Site()
    store, runner = make(tmp_path, site, store)
    batch = await runner.run(Selection(job_sites=False))
    ui, nv = batch["sources"]["uipath"], batch["sources"]["nvidia"]
    assert ui["status"] == "ok" and ui["outcomes"] == {"fetched": 1}
    assert nv["status"] == "ok" and nv["outcomes"] == {"fetched": 1} and nv["details_fetched"] == 1

    [ui] = list(store.iter_postings("uipath"))
    raw = store.load_raw(ui)
    assert raw.kind == "api" and raw.record and raw.detail is None
    [nv] = list(store.iter_postings("nvidia"))
    assert store.load_raw(nv).detail  # Workday list lacks descriptions → detail fetched
    assert "Principal Applied Scientist" in format_table(store, None)
    assert "description" in format_one(store, nv) and "detail fetched: yes" in format_one(store, nv)

    runs = {r["search_run_id"]: r for r in store.get_search_runs(batch["batch_id"])}
    assert {r["outcomes"].get("listed") for r in runs.values()} == {5}
    names = {r["search_name"] for r in store.get_sightings(batch["batch_id"])}
    assert names == {"applied", "mle"}

    site.requests.clear()
    again = await runner.run(Selection(job_sites=False))
    assert again["sources"]["uipath"]["outcomes"] == {"known": 1}
    assert again["sources"]["nvidia"]["outcomes"] == {"known": 1}
    assert not [r for r in site.requests if "/job/" in r]  # known Workday job: no detail request


async def test_rate_limited_company_goes_into_cooldown(tmp_path, store):
    site = Site()
    site.status = 429
    store, runner = make(tmp_path, site, store)
    batch = await runner.run(Selection(sources=["uipath"]))
    assert batch["sources"]["uipath"]["status"] == "blocked"
    assert store.source_health("uipath")["status"] == "cooldown"
