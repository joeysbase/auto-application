"""Platform plugins against saved real API responses (tests/fixtures/<platform>/)."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from auto_app.core.config import CardFilters
from auto_app.core.search import ResolvedSearch
from auto_app.sources.api import keyword_match, location_match, looks_us
from auto_app.platforms import PLATFORMS

FIX = Path(__file__).parents[1] / "fixtures"


def load(rel):
    return json.loads((FIX / rel).read_text())


def search(plugin, **kw) -> ResolvedSearch:
    site_filters = kw.pop("site_filters", {})
    base = dict(name="s", keywords=[], locations=[], posted_within_days=3650, workplace_types=[], employment_types=[],
                experience_levels=[], max_pages=5, known_streak=15, card_filters=CardFilters())
    base.update(kw)
    return ResolvedSearch(**base, site_filters=plugin.SiteFilters(**site_filters))


class FakeCtx:
    def __init__(self, route):
        self.route = route
        self.calls = []

    async def get_json(self, url, params=None):
        self.calls.append(("GET", url, params))
        return self.route("GET", url, None)

    async def post_json(self, url, body):
        self.calls.append(("POST", url, body))
        return self.route("POST", url, body)

    async def cached(self, key, factory):
        return await factory()


async def matching(plugin, board, s, route):
    jobs = await plugin.search(FakeCtx(route), board, s)
    return [j for j in jobs if plugin.local_filter(j, s) is None]


# ---- shared helpers --------------------------------------------------------------------

def test_keyword_and_location_helpers():
    assert keyword_match("Engineer, Machine Learning", ["machine learning engineer"])
    assert not keyword_match("Machine Learning Researcher", ["machine learning engineer"])
    assert looks_us("San Francisco, CA") and looks_us("Remote-Friendly, United States") and looks_us("US, CA, Santa Clara")
    assert not looks_us("Bangalore - Engineering") and not looks_us("London, UK")
    assert location_match(["Remote-Pennsylvania", "United States"], ["United States"])
    assert location_match(["Anywhere"], ["Remote"], remote=True)


# ---- Ashby ---------------------------------------------------------------------------------

async def test_ashby_keywords_location_departments():
    p = PLATFORMS["ashby"]()
    route = lambda m, url, body: load("ashby/uipath.json")  # noqa: E731
    jobs = await matching(p, "uipath", search(p, keywords=["applied scientist"], locations=["United States"]), route)
    assert [j.title for j in jobs] == ["Principal Applied Scientist"]  # the Romanian "Senior Applied Scientist" is excluded
    j = jobs[0]
    assert "United States" in j.locations and j.url.startswith("https://jobs.ashbyhq.com/uipath/")
    assert await matching(p, "uipath", search(p, site_filters={"departments": ["Sales"]}), route)
    assert all(j.record["department"] == "Sales"
               for j in await matching(p, "uipath", search(p, site_filters={"departments": ["Sales"]}), route))


# ---- Greenhouse ----------------------------------------------------------------------------

async def test_greenhouse_us_offices():
    p = PLATFORMS["greenhouse"]()
    route = lambda m, url, body: load("greenhouse/anthropic.json")  # noqa: E731
    jobs = await matching(p, "anthropic", search(p, keywords=["engineer"], locations=["United States"]), route)
    assert [j.title for j in jobs] == ["AI Operations Engineer, Partnerships"]  # Sydney/Tokyo excluded
    fellows = await matching(p, "anthropic", search(p, keywords=["fellows"], locations=["United States"]), route)
    assert len(fellows) == 1  # multi-location incl. "Remote-Friendly, United States"


# ---- Lever -----------------------------------------------------------------------------------

async def test_lever_country_and_commitment():
    p = PLATFORMS["lever"]()
    route = lambda m, url, body: load("lever/palantir.json")  # noqa: E731
    jobs = await matching(p, "palantir", search(p, keywords=["software engineer"], locations=["United States"],
                                                 employment_types=["full_time"]), route)
    assert [j.title for j in jobs] == ["Backend Software Engineer - Application Development"]
    assert jobs[0].posted_at is not None


# ---- Workday --------------------------------------------------------------------------------

NVIDIA = "https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite"


def workday_route(method, url, body):
    if url.endswith("/jobs"):
        return load("workday/nvidia_facets.json") if body["limit"] == 1 else load("workday/nvidia_search.json")
    return load("workday/nvidia_detail.json")


async def test_workday_facets_search_and_detail():
    p = PLATFORMS["workday"]()
    board = p.validate_board(NVIDIA + "/")
    s = search(p, keywords=["machine learning engineer"], locations=["United States"], employment_types=["full_time"],
               posted_within_days=1, site_filters={"facets": {"Job Type": ["New College Graduate"]}})
    ctx = FakeCtx(workday_route)
    jobs = await p.search(ctx, board, s)
    body = [c[2] for c in ctx.calls if c[0] == "POST" and c[2]["limit"] == 20][0]
    assert body["searchText"] == "machine learning engineer"
    assert set(body["appliedFacets"]) == {"locationHierarchy1", "timeType", "workerSubType"}  # all native
    assert len(jobs) == 5
    fresh = [j for j in jobs if p.local_filter(j, s) is None]
    assert [j.posted_text for j in fresh] == ["Posted Today"]  # others are older than 1 day
    assert fresh[0].id.startswith("JR") and fresh[0].url.startswith(NVIDIA + "/job/")
    detail = await p.fetch_detail(ctx, board, fresh[0])
    assert detail["jobPostingInfo"]["jobDescription"]


async def test_workday_unknown_facet_value_explains_choices():
    p = PLATFORMS["workday"]()
    s = search(p, site_filters={"facets": {"Job Type": ["Wizard"]}})
    with pytest.raises(ValueError, match="no value \\['Wizard'\\].*New College Graduate"):
        await p.search(FakeCtx(workday_route), NVIDIA, s)


def test_workday_posted_text():
    from importlib import import_module

    wd = import_module("auto_app.platforms.workday")
    now = datetime(2026, 9, 29, tzinfo=timezone.utc)
    assert wd.posted_from_text("Posted Today", now) == now
    assert wd.posted_from_text("Posted Yesterday", now) == now - timedelta(days=1)
    assert wd.posted_from_text("Posted 3 Days Ago", now) == now - timedelta(days=3)
    assert wd.posted_from_text("Posted 30+ Days Ago", now) == now - timedelta(days=31)
