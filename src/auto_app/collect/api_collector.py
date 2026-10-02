"""Company collection through API plugins: list jobs → local filters → Level 1a → (detail) → save raw.

No browser. HTTP is throttled per company; job-detail requests count against the company's
hourly/daily limits like job pages do for browser sources. docs/step1-collect.md §3.3.
"""

import asyncio
import logging
import random
import time
from collections import Counter

import httpx

from auto_app.collect.collector_base import CollectorBase
from auto_app.collect.filters import reject_reason
from auto_app.collect.guard import HardBlock, SoftFailure
from auto_app.collect.plan import SourceTask
from auto_app.core.models import Card, Outcome, StopReason
from auto_app.core.search import ResolvedSearch
from auto_app.sources.api import RawJob

log = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (compatible; auto-app/0.1; personal job search)"
ATTEMPTS = 3


class HttpContext:
    """Throttled JSON client handed to API plugins. Never spoofs or retries past a block."""

    def __init__(self, client: httpx.AsyncClient, requests_per_second: float, *, fast: bool = False):
        self.client = client
        self.min_interval = 0 if fast else 1 / max(requests_per_second, 0.01)
        self.fast = fast
        self._last = 0.0
        self._cache: dict[str, object] = {}
        self.requests = 0

    async def _throttle(self) -> None:
        wait = self._last + self.min_interval * random.uniform(1, 1.5) - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)
        self._last = time.monotonic()

    async def _request(self, method: str, url: str, **kw):
        last: SoftFailure | None = None
        for attempt in range(ATTEMPTS):
            await self._throttle()
            self.requests += 1
            try:
                r = await self.client.request(method, url, **kw)
            except httpx.HTTPError as e:
                last = SoftFailure(f"{type(e).__name__} for {url}")
            else:
                if r.status_code in (403, 429):
                    raise HardBlock(f"HTTP {r.status_code} from {url}")
                if r.status_code in (404, 410):
                    raise SoftFailure(f"HTTP {r.status_code} for {url}", retryable=False)
                if r.status_code >= 500:
                    last = SoftFailure(f"HTTP {r.status_code} for {url}")
                elif r.status_code >= 400:
                    raise SoftFailure(f"HTTP {r.status_code} for {url}: {r.text[:200]}", retryable=False)
                else:
                    try:
                        return r.json()
                    except ValueError:
                        raise SoftFailure(f"not JSON from {url}", retryable=False) from None
            if attempt + 1 < ATTEMPTS and not self.fast:
                await asyncio.sleep(5 * 2**attempt)
        raise last

    async def get_json(self, url: str, params: dict | None = None):
        return await self._request("GET", url, params=params)

    async def post_json(self, url: str, body: dict):
        return await self._request("POST", url, json=body)

    async def cached(self, key: str, factory):
        if key not in self._cache:
            self._cache[key] = await factory()
        return self._cache[key]


class ApiCollector(CollectorBase):
    def __init__(self, task: SourceTask, runner, batch_id: str, client: httpx.AsyncClient | None = None):
        super().__init__(task.source, runner, batch_id, task.limits)
        self.task = task
        self.plugin = task.plugin
        self._client = client
        self._runner = runner

    async def run(self) -> dict:
        if self.in_cooldown():
            return self.summary()
        client = self._client or httpx.AsyncClient(
            timeout=30, follow_redirects=True, headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            transport=getattr(self._runner, "http_transport", None),
        )
        ctx = HttpContext(client, self.plugin.requests_per_second, fast=self.opts.fast)
        try:
            for search in self.task.searches:
                if reason := self.rate.day_exhausted():
                    log.info("%s: %s; skipping remaining searches until a later run", self.name, reason)
                    break
                await self._run_search(ctx, search)
            if self.status == "ok":
                self.mark_ok()
        except HardBlock as e:
            self.record_block(e)
        finally:
            if self._client is None:
                await client.aclose()
        self.pages_loaded = ctx.requests
        return self.summary()

    async def _run_search(self, ctx: HttpContext, search: ResolvedSearch) -> None:
        params = {**search.record(), "platform": self.task.platform, "board": self.task.board}
        run_id = self.start_run(search.name, params, self.task.board)
        counts: Counter = Counter()
        stop: StopReason | None = None
        before = ctx.requests
        log.info("%s [%s] run %d: %s %s", self.name, search.name, run_id, self.task.platform, self.task.board)
        try:
            try:
                jobs = await self.plugin.search(ctx, self.task.board, search)
            except ValueError as e:  # plugin-reported config problem (e.g. unknown Workday facet)
                raise SoftFailure(str(e), retryable=False) from e
            counts["listed"] = len(jobs)
            rank = 0
            for job in jobs:
                reason = self.plugin.local_filter(job, search)
                if reason:
                    counts["not_matching"] += 1
                    continue
                rank += 1
                card = self._card(job, rank)
                outcome, detail = await self._handle(ctx, card, job, search, run_id)
                counts[outcome] += 1
                self.stats[outcome] += 1
                self.sighting(run_id, search.name, card, outcome, detail)
            stop = StopReason.BUDGET if self.rate.day_exhausted() and counts[Outcome.BUDGET_SKIPPED] else StopReason.DONE
        except SoftFailure as e:
            log.warning("%s run %d stopped: %s", self.name, run_id, e)
            self.error = str(e)
            self.status = "error"
            stop = StopReason.ERROR
        except HardBlock:
            stop = StopReason.BLOCKED
            raise
        except asyncio.CancelledError:
            stop = StopReason.INTERRUPTED
            raise
        finally:
            self.finish_run(run_id, stop or StopReason.ERROR, ctx.requests - before, counts)

    def _card(self, job: RawJob, rank: int) -> Card:
        posted = job.posted_text or (job.posted_at.date().isoformat() if job.posted_at else "")
        lines = [job.title, self.task.display_name or self.name, *job.locations[:3], posted]
        return Card(source=self.name, source_job_id=job.id, url=job.url, page=0, rank=rank,
                    title=job.title, lines=[x for x in lines if x])

    async def _handle(self, ctx, card: Card, job: RawJob, search: ResolvedSearch, run_id: int):
        if reason := reject_reason(card, search.card_filters):
            return Outcome.FILTERED, reason
        if self.known(job.id):
            return Outcome.KNOWN, None
        if self.opts.cards_only:
            return Outcome.CARDS_ONLY, None
        if reason := self.rate.day_exhausted():
            return Outcome.BUDGET_SKIPPED, reason
        detail = None
        if self.plugin.needs_detail:
            await self.rate.acquire()
            self.rate.record()
            try:
                detail = await self.plugin.fetch_detail(ctx, self.task.board, job)
            except SoftFailure as e:
                return Outcome.FETCH_FAILED, str(e)
        path = self.store.save_api_posting(
            self.name, job, detail, platform=self.task.platform, board=self.task.board,
            batch_id=self.batch_id, search_run_id=run_id,
        )
        self.details_fetched += 1
        log.info("%s saved %s (%s) → %s", self.name, job.id, job.title[:60], path)
        return Outcome.FETCHED, None
