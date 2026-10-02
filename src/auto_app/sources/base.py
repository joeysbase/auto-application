"""Site adapter: all site knowledge (URLs, selectors, patterns) behind one interface.

Adapters never sleep, retry, or write to storage; the collect engine does that
around every call.
"""

import json
import logging
import re
from dataclasses import dataclass, field, replace
from typing import Awaitable, Callable, ClassVar, Literal
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Page, Response

from pydantic import BaseModel

from auto_app.core.models import CapturedResponse, Card, CommonPosting, RawCapture, RawPosting, utcnow
from auto_app.core.search import ResolvedSearch
from auto_app.sources.common import js
from auto_app.sources.filters import Filter, NoSiteFilters

log = logging.getLogger(__name__)

SessionStatus = Literal["ok", "needs_login", "unknown"]


@dataclass(frozen=True)
class SiteQuery:
    source: str
    search_name: str
    url: str
    params: dict  # generic query as configured, recorded with the search run


@dataclass(frozen=True)
class SourceSpec:
    name: str
    base_url: str
    site_hosts: tuple[str, ...]
    # "{base}" in the templates below is replaced by base_url
    login_url: str
    session_check_url: str
    detail_url_template: str  # "{base}" and "{id}"
    logged_in_selectors: tuple[str, ...] = ()
    logged_out_texts: tuple[str, ...] = ("sign in", "log in", "login")
    login_url_markers: tuple[str, ...] = ("/login", "/checkpoint", "/authwall", "/signin", "/sign-in", "/uas/")
    pagination: Literal["url", "scroll"] = "url"
    page_param: str | None = None
    page_param_kind: Literal["offset", "page"] = "page"
    page_size: int = 25
    list_selectors: tuple[str, ...] = ()
    card_container_selectors: tuple[str, ...] = ()
    card_id_attributes: tuple[str, ...] = ()  # "attr" (value = job id) or "attr~regex" (group 1 = job id)
    job_link_patterns: tuple[str, ...] = ()
    promoted_texts: tuple[str, ...] = ()
    detail_ready_selectors: tuple[str, ...] = ("h1",)
    capture_url_patterns: tuple[str, ...] = ()
    apply_selectors: tuple[str, ...] = ()
    selector_version: str = "0"
    extra: dict = field(default_factory=dict)

    def url(self, template: str, **kw) -> str:
        return template.format(base=self.base_url.rstrip("/"), **kw)


class SourceAdapter:
    """A browser plugin (BrowserSource): drives Chrome through a site's search and job pages."""

    kind: ClassVar[str] = "browser"
    company: ClassVar[str | None] = None  # set for a single-employer (company) site; None = a job site
    platform: ClassVar[str | None] = None
    board: ClassVar[str | None] = None
    filters: ClassVar[dict[str, Filter]] = {}  # generic field → Native/Local; others deferred
    SiteFilters: ClassVar[type[BaseModel]] = NoSiteFilters
    sorted_by_date: ClassVar[bool] = True  # False → early stop disabled
    needs_login: ClassVar[bool] = True
    spec: SourceSpec

    def __init__(self, spec: SourceSpec):
        self.spec = spec

    @property
    def name(self) -> str:
        return self.spec.name

    def with_base_url(self, base_url: str | None) -> "SourceAdapter":
        if base_url:
            host = urlparse(base_url).hostname or ""
            self.spec = replace(self.spec, base_url=base_url, site_hosts=(*self.spec.site_hosts, host))
        return self

    # ---- queries -----------------------------------------------------------------

    def build_queries(self, search: ResolvedSearch) -> list[SiteQuery]:
        """One query per keyword × location (or the pasted `site_url`)."""
        base = search.record()
        if search.site_url:
            return [SiteQuery(self.name, search.name, search.site_url, {**base, "site_url": search.site_url})]
        out = []
        for kw in search.keywords or [""]:
            for loc in search.locations or [""]:
                params = {**base, "keywords": kw, "location": loc}
                out.append(SiteQuery(self.name, search.name, self.search_url(kw, loc, search), params))
        return out

    def search_url(self, keywords: str, location: str, search: ResolvedSearch) -> str:
        raise NotImplementedError

    def page_url(self, first_url: str, page_index: int) -> str:
        """URL of results page `page_index` (0-based), derived from the page-1 URL."""
        if not self.spec.page_param:
            raise ValueError(f"{self.name}: url pagination needs page_param")
        value = page_index * self.spec.page_size if self.spec.page_param_kind == "offset" else page_index + 1
        return set_query_param(first_url, self.spec.page_param, str(value))

    def detail_url(self, job_id: str) -> str:
        return self.spec.url(self.spec.detail_url_template, id=job_id)

    def job_id_from_url(self, url: str) -> str | None:
        for p in self.spec.job_link_patterns:
            m = re.search(p, url)
            if m:
                return m.group(1)
        return None

    # ---- session -----------------------------------------------------------------

    def is_login_url(self, url: str) -> bool:
        path = urlparse(url).path.lower()
        return any(m in path for m in self.spec.login_url_markers)

    async def check_session(self, page: Page) -> SessionStatus:
        if self.is_login_url(page.url):
            return "needs_login"
        if self.spec.logged_in_selectors:
            try:
                await page.wait_for_selector(", ".join(self.spec.logged_in_selectors), state="attached", timeout=10_000)
                return "ok"
            except PlaywrightError:
                pass
        if self.spec.logged_out_texts and await page.evaluate(js.VISIBLE_TEXT_PRESENT, list(self.spec.logged_out_texts)):
            return "needs_login"
        return "unknown"

    # ---- results list ------------------------------------------------------------

    async def read_cards(self, page: Page, page_index: int) -> list[Card]:
        items = await page.evaluate(
            js.READ_CARDS,
            {
                "listSelectors": list(self.spec.list_selectors),
                "containerSelectors": list(self.spec.card_container_selectors),
                "idAttributes": list(self.spec.card_id_attributes),
                "linkPatterns": list(self.spec.job_link_patterns),
                "promotedTexts": list(self.spec.promoted_texts),
                "maxHtml": 60_000,
            },
        )
        cards = []
        for rank, it in enumerate(items, start=1):
            cards.append(
                Card(
                    source=self.name,
                    source_job_id=str(it["id"]),
                    url=it["href"] or self.detail_url(str(it["id"])),
                    page=page_index,
                    rank=rank,
                    is_promoted=it["promoted"],
                    title=it["title"],
                    lines=it["lines"],
                    attrs=it["attrs"],
                    html=it["html"],
                )
            )
        return cards

    async def end_of_results(self, page: Page) -> bool:
        """True when the page shows that the matching results have ended (e.g. the rest are only "related"
        suggestions); the engine then reads no further pages of this query. Default: never."""
        return False

    async def scroll_step(self, page: Page, fraction: float = 0.85) -> bool:
        return await page.evaluate(js.SCROLL_STEP, {"listSelectors": list(self.spec.list_selectors), "fraction": fraction})

    async def collect_cards(
        self, page: Page, page_index: int, pause: Callable[[], Awaitable[None]], max_steps: int = 40
    ) -> list[Card]:
        """Scroll the list step by step, reading cards after each step.

        Reading while scrolling handles both lazy rendering (cards filled in when
        scrolled into view) and virtualized lists (cards removed when scrolled out).
        """
        merged: dict[str, Card] = {}
        for _ in range(max_steps):
            for c in await self.read_cards(page, page_index):
                old = merged.get(c.source_job_id)
                if old is None or len(c.lines) > len(old.lines):  # keep the most fully rendered version
                    merged[c.source_job_id] = c  # dict keeps first-seen order
            if not await self.scroll_step(page):
                break
            await pause()
        return [c.model_copy(update={"rank": i}) for i, c in enumerate(merged.values(), start=1)]

    # ---- detail ------------------------------------------------------------------

    async def wait_detail_ready(self, page: Page) -> bool:
        ready = True
        try:
            await page.wait_for_selector(", ".join(self.spec.detail_ready_selectors), state="attached", timeout=15_000)
        except PlaywrightError:
            ready = False
        try:
            await page.wait_for_load_state("networkidle", timeout=6_000)
        except PlaywrightError:
            pass
        return ready

    async def snapshot(
        self,
        page: Page,
        *,
        job_id: str,
        requested_url: str,
        response: Response | None,
        ready: bool,
        payloads: list[CapturedResponse],
    ) -> RawCapture:
        html = await page.content()
        text = await page.evaluate("() => document.body ? document.body.innerText : ''")
        embedded = _parse_embedded(await page.evaluate(js.EMBEDDED))
        apply = await page.evaluate(js.FIND_APPLY, {"selectors": list(self.spec.apply_selectors)})
        apply_text = apply["text"] if apply else None
        apply_href = apply["href"] if apply else None
        return RawCapture(
            source=self.name,
            source_job_id=job_id,
            requested_url=requested_url,
            final_url=page.url,
            http_status=response.status if response else None,
            fetched_at=utcnow(),
            ready=ready,
            detail_html=html,
            page_text=text,
            payloads=payloads,
            embedded=embedded,
            apply_text=apply_text,
            apply_href=apply_href,
            apply_kind=self.classify_apply(apply_text, apply_href) if apply else "unknown",
            meta={
                "selector_version": self.spec.selector_version,
                "html_chars": len(html),
                "text_chars": len(text),
                "payload_count": len(payloads),
            },
        )

    # ---- parse (step 1b; no network) -------------------------------------------------

    parser_version: ClassVar[str] = "1"

    def parse(self, raw: "RawPosting") -> "CommonPosting":
        """Raw page → common fields. Default: the page's schema.org JobPosting (JSON-LD)."""
        from auto_app.sources.parsing import parse_jsonld

        return parse_jsonld(raw)

    def classify_apply(self, text: str | None, href: str | None) -> str:
        t = (text or "").lower()
        if "easy apply" in t:
            return "easy_apply"
        if href and not self.is_site_host(href):
            return "external"
        return "onsite"

    def is_site_host(self, url: str) -> bool:
        host = (urlparse(url).hostname or "").lower()
        return any(host == h or host.endswith("." + h) for h in self.spec.site_hosts)


def set_query_param(url: str, key: str, value: str) -> str:
    parts = urlparse(url)
    q = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != key]
    q.append((key, value))
    return urlunparse(parts._replace(query=urlencode(q)))


def _parse_embedded(raw: dict) -> dict:
    def load(s):
        try:
            return json.loads(s)
        except (TypeError, ValueError):
            return s

    return {
        "jsonld": [load(s) for s in raw.get("jsonld", [])],
        "next_data": load(raw["next_data"]) if raw.get("next_data") else None,
        "json_scripts": [{"id": s["id"], "body": load(s["text"])} for s in raw.get("json_scripts", [])],
        "code_blocks": [{"id": c["id"], "body": load(c["text"])} for c in raw.get("code_blocks", [])],
    }
