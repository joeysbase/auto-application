"""Data shapes shared across stages."""

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Outcome(StrEnum):
    KNOWN = "known"
    FILTERED = "filtered"
    FETCHED = "fetched"
    FETCH_FAILED = "fetch_failed"
    BUDGET_SKIPPED = "budget_skipped"
    CARDS_ONLY = "cards_only"


class StopReason(StrEnum):
    DONE = "done"
    EARLY_STOP = "early_stop"
    MAX_PAGES = "max_pages"
    BUDGET = "budget"
    BLOCKED = "blocked"
    EMPTY = "empty"
    ERROR = "error"
    INTERRUPTED = "interrupted"  # stopped by the user (Ctrl+C)


class Card(BaseModel):
    """One job card as shown in a search results list."""

    source: str
    source_job_id: str
    url: str
    page: int
    rank: int
    is_promoted: bool = False
    title: str | None = None
    lines: list[str] = []  # visible text lines of the card, in order
    attrs: dict[str, str] = {}  # data-* attributes of the card element
    html: str | None = Field(default=None, exclude=True)


class CapturedResponse(BaseModel):
    url: str
    status: int
    content_type: str
    body: Any
    truncated: bool = False


class RawCapture(BaseModel):
    """Everything the site delivered for one job detail page, uninterpreted."""

    source: str
    source_job_id: str
    requested_url: str
    final_url: str
    http_status: int | None
    fetched_at: datetime
    ready: bool  # a detail-ready selector appeared before timeout
    detail_html: str = Field(exclude=True)
    page_text: str = Field(exclude=True)  # document.body.innerText
    payloads: list[CapturedResponse] = Field(default=[], exclude=True)
    embedded: dict[str, Any] = Field(default={}, exclude=True)
    apply_text: str | None = None
    apply_href: str | None = None
    apply_kind: str = "unknown"  # easy_apply | external | onsite | unknown
    apply_redirects: list[str] = []
    screenshot: bytes | None = Field(default=None, exclude=True)
    meta: dict[str, Any] = {}


class Salary(BaseModel):
    min: float | None = None
    max: float | None = None
    currency: str | None = None  # "USD"
    period: str | None = None  # year | hour | month | week
    text: str | None = None  # as shown
    source: str | None = None  # where it came from: header | structured | description
    plausible: bool = True  # False e.g. "$90/yr" (likely a typo for $90K): kept as shown, flagged


class CommonPosting(BaseModel):
    """A posting's fields in one shape for every source (Step 1b parse output)."""

    source: str
    source_job_id: str
    title: str
    company: str | None = None
    locations: list[str] = []  # as shown, one entry per location
    workplace_type: str = "unknown"  # onsite | hybrid | remote | unknown
    employment_type: str = "unknown"  # full_time | part_time | contract | temporary | internship | unknown
    seniority: str | None = None  # as stated by the source
    salary: Salary | None = None
    posted_at: datetime | None = None
    posted_at_precision: str | None = None  # exact | day | approx
    posted_text: str | None = None
    reposted: bool = False
    reposted_at: datetime | None = None
    applicants: int | None = None
    applicants_text: str | None = None
    apply_url: str | None = None
    apply_kind: str = "unknown"  # easy_apply | external | onsite | unknown
    url: str | None = None  # the posting's page on its source
    req_id: str | None = None
    description_text: str = ""
    description_html: str | None = None
    extras: dict[str, Any] = {}  # source-specific (department, promoted, already applied …)


class RawPosting(BaseModel):
    """Everything saved for one posting, loaded for a parser. Parsers never touch the network."""

    source: str
    source_job_id: str
    kind: str  # browser | api
    posting: dict
    capture: dict
    card: dict
    company_hint: str | None = None  # company sources: the config's display name
    # browser
    page_text: str = ""
    html: str = ""
    payloads: list = []
    embedded: dict = {}
    # api
    record: dict = {}
    detail: dict | None = None
