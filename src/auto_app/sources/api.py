"""API sources: sources searched through a JSON API — usually one company's job board on a platform
(Ashby, Greenhouse, Lever, Workday …; bases in auto_app/platforms, one plugin per company).

A plugin supplies site knowledge only: how to list a company's jobs, how to test a job
against a search, and (if the list lacks it) how to fetch a job's detail. The engine
(collect/api_collector.py) owns HTTP pacing, limits, known checks, storage and records.
"""

import re
from datetime import datetime, timedelta, timezone
from typing import Any, ClassVar, Protocol

from pydantic import BaseModel

from auto_app.core.config import SourceLimits
from auto_app.core.models import CommonPosting, RawPosting
from auto_app.core.search import ResolvedSearch
from auto_app.sources.filters import Filter, NoSiteFilters


class RawJob(BaseModel):
    id: str  # unique within the board
    url: str  # public job page
    title: str
    locations: list[str] = []  # every location string the record gives (incl. countries)
    posted_at: datetime | None = None
    posted_text: str | None = None  # as shown, e.g. "Posted Yesterday"
    apply_url: str | None = None
    remote: bool | None = None
    record: dict[str, Any]  # the platform's record, stored as-is


class ApiContext(Protocol):
    """HTTP helper the engine hands to plugins (throttled; raises the engine's block/failure errors)."""

    async def get_json(self, url: str, params: dict | None = None) -> Any: ...
    async def post_json(self, url: str, body: dict) -> Any: ...
    async def cached(self, key: str, factory) -> Any: ...


class ApiSource:
    name: ClassVar[str]  # the source name: config key under `sources:`, storage name, `-s NAME`
    kind: ClassVar[str] = "api"
    # Single-employer (company) sources: the employer's display name. None = a job site (many employers).
    company: ClassVar[str | None] = None
    platform: ClassVar[str | None] = None  # set by the platform base (workday, ashby …)
    board: ClassVar[str | None] = None  # the company's board on the platform (Workday: its careers URL)
    filters: ClassVar[dict[str, Filter]] = {}
    SiteFilters: ClassVar[type[BaseModel]] = NoSiteFilters
    requests_per_second: ClassVar[float] = 1.0
    needs_detail: ClassVar[bool] = False  # list lacks descriptions → fetch_detail per new job
    default_limits: ClassVar[SourceLimits | None] = None
    board_help: ClassVar[str] = "the company's id on the platform"

    def validate_board(self, board: str | None) -> str:
        if not board:
            raise ValueError(f"`board` is required ({self.board_help})")
        return board

    async def search(self, ctx: ApiContext, board: str, search: ResolvedSearch) -> list[RawJob]:
        raise NotImplementedError

    def local_filter(self, job: RawJob, search: ResolvedSearch) -> str | None:
        """Why `job` doesn't match `search` (None = it matches). Default: shared generic checks."""
        return generic_local_filter(job, search, self.filters)

    async def fetch_detail(self, ctx: ApiContext, board: str, job: RawJob) -> dict | None:
        return None

    # ---- parse (step 1b; no network) -------------------------------------------------

    parser_version: ClassVar[str] = "1"

    def parse(self, raw: RawPosting) -> CommonPosting:
        """Saved record (+ detail) → common fields. Each platform implements this."""
        from auto_app.sources.parsing import ParseError

        raise ParseError(f"the {getattr(self, 'name', type(self).__name__)} plugin has no parser")


# ---- shared matching helpers ---------------------------------------------------------

US_ALIASES = {"united states", "united states of america", "usa", "us", "u.s.", "u.s.a."}
US_STATES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA",
    "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK",
    "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY", "DC",
}
_WORD = re.compile(r"[a-z0-9+#]+")


def words(text: str) -> set[str]:
    return set(_WORD.findall(text.lower()))


def keyword_match(title: str, keywords: list[str]) -> bool:
    """Any keyword whose words all appear in the title (order-insensitive)."""
    if not keywords:
        return True
    t = words(title)
    return any(words(k) <= t for k in keywords)


def looks_us(text: str) -> bool:
    low = text.lower().strip()
    if any(a in low for a in ("united states", "usa", "u.s.")):
        return True
    parts = [p.strip() for p in re.split(r"[,;/|()-]", text) if p.strip()]
    return any(p.upper() in US_STATES and len(p) == 2 for p in parts) or any(p.lower() in US_ALIASES for p in parts)


def location_match(job_locations: list[str], wanted: list[str], remote: bool | None = None) -> bool:
    wanted = [w for w in wanted if w.strip()]
    if not wanted:
        return True
    for w in wanted:
        wl = w.lower().strip()
        for loc in job_locations:
            if wl in US_ALIASES and looks_us(loc):
                return True
            if wl == "remote" and ("remote" in loc.lower() or remote):
                return True
            if wl in loc.lower():
                return True
    return False


def posted_within(posted_at: datetime | None, days: int | None) -> bool:
    if posted_at is None or not days:
        return True  # unknown dates pass (never miss jobs)
    if posted_at.tzinfo is None:
        posted_at = posted_at.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - posted_at <= timedelta(days=days)


def generic_local_filter(job: RawJob, search: ResolvedSearch, filters: dict[str, Filter]) -> str | None:
    """Checks only the fields the plugin declared as Local."""
    local = {k for k, f in filters.items() if f.level == "local"}
    if "keywords" in local and not keyword_match(job.title, search.keywords):
        return "keywords"
    if "locations" in local and not location_match(job.locations, search.locations, job.remote):
        return "location"
    if "posted_within_days" in local and not posted_within(job.posted_at, search.posted_within_days):
        return "posted date"
    return None


def in_any(value: str | None, wanted: list[str]) -> bool:
    """Case-insensitive: does `value` equal (or contain) any wanted name? Empty `wanted` = anything."""
    if not wanted:
        return True
    v = (value or "").lower()
    return any(w.lower() == v or w.lower() in v for w in wanted)


def mapped_mismatch(raw_value: str | None, mapping: dict[str, str], wanted: list[str]) -> bool:
    """True only when the value is known and not wanted (unknown values pass: never miss jobs)."""
    if not wanted or raw_value is None:
        return False
    generic = mapping.get(raw_value)
    return generic is not None and generic not in wanted


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
