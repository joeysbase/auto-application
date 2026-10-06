"""Collection engine: batch → tasks (one per source) → search runs → jobs → (Level 1a) → raw fetch."""

import asyncio
import hashlib
import logging
import time
from collections import Counter
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime
from typing import Awaitable, Callable
from urllib.parse import urlparse

from playwright.async_api import BrowserContext, Page, Playwright, Response, async_playwright
from playwright.async_api import Error as PlaywrightError

from auto_app.collect import apply_link
from auto_app.collect.browser import BrowserSession, ResponseCapture
from auto_app.collect.collector_base import CollectorBase
from auto_app.collect.filters import reject_reason
from auto_app.collect.goal import RoundGoal
from auto_app.collect.guard import ChallengeDetected, HardBlock, HealthGuard, NeedsLogin, SoftFailure
from auto_app.collect.pacer import Pacer
from auto_app.collect.plan import Selection, build_tasks
from auto_app.collect.store import FileStore
from auto_app.core.config import AppConfig
from auto_app.core.logging import add_file_log
from auto_app.core.models import Card, Outcome, StopReason, utcnow
from auto_app.core.notify import notify
from auto_app.core.search import ResolvedSearch
from auto_app.settings import Paths
from auto_app.sources.base import SiteQuery, SourceAdapter
from auto_app.sources.registry import Registry, default_registry

log = logging.getLogger(__name__)

FETCH_ATTEMPTS = 3
SCROLL_STEPS_PER_PAGE = {"url": 40, "scroll": 8}


@dataclass
class CollectOptions:
    headless: bool = False
    cards_only: bool = False  # read cards and record sightings, fetch nothing
    ignore_known: bool = False  # re-fetch even if the posting exists
    max_pages: int | None = None
    screenshots: bool = False  # a PNG per job page (~130 KB); on for `probe`, opt-in for collect
    card_html: bool = False  # keep each card's HTML with its sighting (for debugging card reading)
    interactive: bool = False  # pause after opening each search so filters can be set by hand
    capture_all_json: bool = False  # capture every JSON response from the site (for spikes)
    resolve_apply: bool = True
    retry: bool = True
    force: bool = False  # ignore cooldowns
    manual_checks: bool = False  # hand security checks to the user for every source this run
    fast: bool = False  # no delays (tests)
    goal: "RoundGoal | None" = None  # a round's goal (time cap, volume caps); None: as before (collect/goal.py)
    source_goals: dict | None = None  # per source (`serve`'s config): {source: RoundGoal}, over `goal`
    context_hook: Callable[[BrowserContext], Awaitable[None]] | None = None


def _jsonable(v):
    """Options as stored with the batch: goals (dataclasses), also per source, as plain dicts."""
    if is_dataclass(v):
        return asdict(v)
    if isinstance(v, dict):
        return {k: _jsonable(x) for k, x in v.items()}
    return v


def new_batch_id() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


class CollectionRunner:
    def __init__(
        self,
        cfg: AppConfig,
        paths: Paths,
        store: FileStore,
        opts: CollectOptions,
        registry: Registry | None = None,
        http_transport=None,  # tests: an httpx transport for API plugins
    ):
        self.http_transport = http_transport
        self.cfg = cfg
        self.paths = paths
        self.store = store
        self.opts = opts
        self.registry = registry or default_registry()

    async def run(self, selection: Selection | list[str] | None = None, search_names: list[str] | None = None) -> dict:
        if isinstance(selection, list):  # a list of source names
            selection = Selection(sources=selection, search_names=search_names)
        sel = selection or Selection(search_names=search_names)
        tasks = build_tasks(self.cfg, self.registry, sel)
        batch_id = new_batch_id()
        handler = add_file_log(self.store.log_path(batch_id))
        batch = {
            "batch_id": batch_id,
            "started_at": utcnow(),
            "config_hash": hashlib.sha1(self.cfg.model_dump_json().encode()).hexdigest()[:12],
            "options": {k: _jsonable(v) for k, v in vars(self.opts).items() if k != "context_hook"},
            "sources": {},
        }
        self.store.write_batch(batch_id, batch)
        log.info("batch %s → %s (log %s)", batch_id, self.store.describe(), self.store.log_path(batch_id))
        for t in tasks:
            for w in t.warnings:
                log.warning("%s: %s", t.source, w)
        collectors: list[CollectorBase] = []
        try:
            from auto_app.collect.api_collector import ApiCollector

            collectors = [ApiCollector(t, self, batch_id) for t in tasks if t.is_api]
            async with async_playwright() as pw:
                collectors += [
                    SourceCollector(t.plugin, self, pw, batch_id, t.searches, t.limits, name=t.source)
                    for t in tasks
                    if not t.is_api
                ]
                if self.opts.interactive:  # one source at a time so prompts don't interleave
                    results = [await c.run() for c in collectors]
                else:
                    results = await asyncio.gather(*(c.run() for c in collectors))
        except (asyncio.CancelledError, KeyboardInterrupt):
            # Stopped by the user: every saved job is already committed; record how far each source got.
            for c in collectors:
                if c.status == "ok":
                    c.status = "interrupted"
            batch["sources"] = {c.name: c.summary() for c in collectors}
            batch["finished_at"] = utcnow()
            batch["interrupted"] = True
            self.store.write_batch(batch_id, batch)
            log.warning("batch %s interrupted; progress so far is saved", batch_id)
            raise
        finally:
            logging.getLogger().removeHandler(handler)
        batch["sources"] = {r["source"]: r for r in results}
        batch["finished_at"] = utcnow()
        self.store.write_batch(batch_id, batch)
        return batch

    async def probe(self, source: str, job_id: str) -> dict:
        """Fetch one job's detail page (browser sources), overwriting any saved copy."""
        info = self.registry.get(source)
        if info.kind != "browser":
            raise ValueError(f"probe works for browser sources; {source} is collected through an API")
        limits = self.cfg.limits(source, info.cls)
        adapter = self.registry.create(source, limits.base_url)
        batch_id = "probe-" + new_batch_id()
        async with async_playwright() as pw:
            collector = SourceCollector(adapter, self, pw, batch_id, [], limits, name=source)
            return await collector.probe(job_id)


class SourceCollector(CollectorBase):
    """Browser sources: job sites (LinkedIn …) and company browser plugins."""

    def __init__(
        self,
        adapter: SourceAdapter,
        runner: CollectionRunner,
        pw: Playwright,
        batch_id: str,
        searches: list[ResolvedSearch],
        limits,
        name: str | None = None,
    ):
        super().__init__(name or adapter.name, runner, batch_id, limits)
        self.adapter = adapter
        self.pw = pw
        self.searches = searches
        self.pacer = Pacer(self.limits, fast=self.opts.fast)
        self.guard = HealthGuard(adapter)
        self.results: Page | None = None
        self.detail: Page | None = None
        self.capture: ResponseCapture | None = None

    # ---- entry points ---------------------------------------------------------------

    async def run(self) -> dict:
        if self.in_cooldown():
            return self.summary()
        async with self._session():
            try:
                await self._check_session()
                if self.opts.retry and not self.opts.cards_only:
                    await self._retry_pass()
                queries = [(search, query) for search in self.searches for query in self.adapter.build_queries(search)]
                for i, (search, query) in enumerate(queries):
                    if reason := self.rate.day_exhausted():
                        log.info("%s: %s; skipping remaining searches until a later run", self.name, reason)
                        self.skipped_searches += len(queries) - i
                        break
                    if self.budget is not None:
                        if (hit := self.budget.exhausted()) is not None:
                            log.info("%s: round goal reached (%s); %d search(es) left for the next round",
                                     self.name, hit, len(queries) - i)
                            self.budget.skipped_searches += len(queries) - i
                            break
                        self.budget.start_query(len(queries) - i)  # its share of the time left
                    await self._run_query(search, query)
                self.mark_ok()
            except HardBlock as e:
                await self._on_block(e)
        return self.summary()

    async def probe(self, job_id: str) -> dict:
        self.rate_limited = False  # a single, manual fetch
        async with self._session():
            try:
                await self._check_session()
                run_id = self.start_run("__probe__", {"job_id": job_id}, self.adapter.detail_url(job_id))
                card = Card(source=self.name, source_job_id=job_id, url=self.adapter.detail_url(job_id), page=0, rank=0)
                outcome, detail = await self._fetch_card(card, run_id)
                self.sighting(run_id, "__probe__", card, outcome, detail)
                self.finish_run(run_id, StopReason.DONE, 0, Counter({outcome: 1}))
                self.stats[outcome] += 1
            except HardBlock as e:
                await self._on_block(e)
        return self.summary()

    # ---- session ----------------------------------------------------------------------

    @asynccontextmanager
    async def _session(self):
        profile = self.paths.profile(self.name)
        async with BrowserSession(self.pw, profile, headless=self.opts.headless, context_hook=self.opts.context_hook) as bs:
            self.results = bs.results
            self.detail = await bs.detail_page()
            spec = self.adapter.spec
            self.capture = ResponseCapture(self.detail, spec.capture_url_patterns, spec.site_hosts, self.opts.capture_all_json)
            yield

    async def _check_session(self) -> None:
        if not self.adapter.needs_login:
            return
        url = self.adapter.spec.url(self.adapter.spec.session_check_url)
        try:
            await self._navigate(self.results, url)
        except SoftFailure as e:
            raise HardBlock(f"session check page failed to load: {e}") from e
        status = await self.adapter.check_session(self.results)
        if status == "needs_login":
            raise NeedsLogin(f"not logged in (checked {self.results.url}); run `auto-app source login {self.name}`")
        if status == "unknown":
            log.warning("%s: could not confirm login state; continuing", self.name)
        else:
            log.info("%s: session ok", self.name)

    async def _on_block(self, e: HardBlock) -> None:
        await self._artifacts("needs-login" if isinstance(e, NeedsLogin) else "blocked")
        self.record_block(e)

    # ---- navigation -------------------------------------------------------------------

    async def _navigate(self, page: Page, url: str) -> Response | None:
        await self.pacer.before_navigation()
        try:
            resp = await page.goto(url, wait_until="domcontentloaded", timeout=45_000)
        except PlaywrightError as e:
            raise SoftFailure(f"navigation failed: {e.message.splitlines()[0]}") from e
        self.pages_loaded += 1
        try:
            await self.guard.check(page, resp)
        except ChallengeDetected as e:
            if not (self.opts.manual_checks or self.limits.manual_checks):
                raise
            await self._wait_for_manual_check(page, e)
            if urlparse(page.url).path != urlparse(url).path:  # site didn't return us to the page
                return await self._navigate(page, url)
            return None
        return resp

    async def _wait_for_manual_check(self, page: Page, e: ChallengeDetected) -> None:
        """Bring the window forward and wait for the user to complete the check. Never solves it."""
        self.manual_checks += 1
        if self.manual_checks > self.limits.max_manual_checks:
            raise HardBlock(f"{self.limits.max_manual_checks} security checks this run; stopping ({e})")
        timeout = self.limits.manual_check_timeout_s
        log.warning("%s: security check — complete it in the browser window (waiting up to %.0f s): %s", self.name, timeout, e)
        notify("auto-app", f"{self.name}: complete the security check in the browser window")
        try:
            await page.bring_to_front()
        except PlaywrightError:
            pass
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            await asyncio.sleep(0.2 if self.opts.fast else 2)
            try:
                if self.adapter.is_login_url(page.url):
                    raise NeedsLogin(f"security check led to login: {page.url}")
                if not await self.guard.detect_challenge(page):
                    log.info("%s: security check cleared; continuing", self.name)
                    await self.pacer.before_navigation()
                    return
            except PlaywrightError:
                continue  # page is navigating
        raise HardBlock(f"security check not completed within {timeout:.0f} s ({e})")

    # ---- queries ----------------------------------------------------------------------

    async def _run_query(self, search: ResolvedSearch, query: SiteQuery) -> None:
        run_id = self.start_run(search.name, query.params, query.url)
        counts: Counter = Counter()
        pages_loaded = 0
        stop: StopReason | None = None
        log.info("%s [%s] run %d: %s", self.name, search.name, run_id, query.url)
        try:
            await self._navigate(self.results, query.url)
            pages_loaded = 1
            first_url = query.url
            if self.opts.interactive:
                await asyncio.to_thread(
                    input,
                    f"\n[{self.name}] Adjust the search/filters in the browser if needed, then press Enter to collect… ",
                )
                first_url = self.results.url
                log.info("%s: collecting from %s", self.name, first_url)

            seen: set[str] = set()
            streak = 0
            page_index = 0
            steps = SCROLL_STEPS_PER_PAGE[self.adapter.spec.pagination]
            early_stop = getattr(self.adapter, "sorted_by_date", True)
            while True:
                cards = await self.adapter.collect_cards(self.results, page_index, self.pacer.scroll_pause, steps)
                new = [c for c in cards if c.source_job_id not in seen]
                log.info("%s run %d page %d: %d cards, %d new", self.name, run_id, page_index, len(cards), len(new))
                if not cards and page_index == 0 and await self.adapter.end_of_results(self.results):
                    log.info("%s run %d: no exact matches for this search", self.name, run_id)
                    stop = StopReason.DONE
                    break
                if not cards and page_index == 0:
                    log.warning("%s run %d: no cards found on first page (selectors may be stale)", self.name, run_id)
                    await self._artifacts(f"empty-run{run_id}", pages=[self.results])
                    stop = StopReason.EMPTY
                    break
                if not new:
                    stop = StopReason.DONE
                    break
                if self.budget is not None:
                    self.budget.pages += 1
                for card in new:
                    if self.budget is not None and (over := self.budget.query_over()) is not None:
                        stop = over  # the round's time / volume (or this search's share) — not caught up
                        break
                    seen.add(card.source_job_id)
                    outcome, detail = await self._handle_card(card, search, run_id)
                    counts[outcome] += 1
                    self.stats[outcome] += 1
                    if self.budget is not None and outcome == Outcome.FETCHED:
                        self.budget.new_postings += 1
                    self.sighting(run_id, search.name, card, outcome, detail)
                    if outcome == Outcome.KNOWN and not card.is_promoted:
                        streak += 1
                    elif outcome != Outcome.FILTERED and outcome != Outcome.KNOWN:
                        streak = 0
                    if early_stop and streak >= search.known_streak:
                        stop = StopReason.EARLY_STOP
                        break
                if stop:
                    break
                if self.rate.day_exhausted():
                    stop = StopReason.BUDGET
                    break
                if await self.adapter.end_of_results(self.results):  # the site says the exact matches end here
                    stop = StopReason.DONE
                    break
                if page_index + 1 >= (self.opts.max_pages or search.max_pages):
                    stop = StopReason.MAX_PAGES
                    break
                if self.budget is not None and (over := self.budget.query_over()) is not None:
                    stop = over
                    break
                page_index += 1
                if self.adapter.spec.pagination == "url":
                    await self._navigate(self.results, self.adapter.page_url(first_url, page_index))
                    pages_loaded += 1
        except SoftFailure as e:
            log.warning("%s run %d stopped: %s", self.name, run_id, e)
            stop = StopReason.ERROR
            self.guard.record_soft_failure(e)
        except HardBlock:
            stop = StopReason.BLOCKED
            raise
        except asyncio.CancelledError:
            stop = StopReason.INTERRUPTED
            raise
        finally:
            self.finish_run(run_id, stop or StopReason.ERROR, pages_loaded, counts)

    async def _retry_pass(self) -> None:
        candidates = self.store.retry_candidates(
            self.name, within_days=self.cfg.retry.retry_within_days, max_attempts=self.cfg.retry.max_fetch_attempts
        )
        if not candidates:
            return
        cap = max(1, int(self.limits.max_details_per_hour * self.cfg.retry.max_share_of_hourly))
        log.info("%s: retrying %d of %d previously failed/skipped cards (cap %d per run)",
                 self.name, min(cap, len(candidates)), len(candidates), cap)
        run_id = self.start_run("__retry__", {"count": len(candidates), "cap": cap}, None)
        counts: Counter = Counter()
        stop = StopReason.DONE if len(candidates) <= cap else StopReason.BUDGET
        for r in candidates[:cap]:
            if self.rate.day_exhausted():
                stop = StopReason.BUDGET
                break
            card = Card(
                source=self.name,
                source_job_id=r["source_job_id"],
                url=r["url"],
                page=r.get("page") or 0,
                rank=r.get("rank") or 0,
                is_promoted=r.get("is_promoted", False),
                title=r.get("title"),
                lines=r.get("lines", []),
                attrs=r.get("attrs", {}),
            )
            outcome, detail = await self._fetch_card(card, run_id)
            counts[outcome] += 1
            self.stats[outcome] += 1
            self.sighting(run_id, "__retry__", card, outcome, detail)
        self.finish_run(run_id, stop, 0, counts)

    # ---- cards ------------------------------------------------------------------------

    async def _handle_card(self, card: Card, search: ResolvedSearch, run_id: int) -> tuple[Outcome, str | None]:
        if reason := reject_reason(card, search.card_filters):
            return Outcome.FILTERED, reason
        if self.known(card.source_job_id):
            return Outcome.KNOWN, None
        if self.opts.cards_only:
            return Outcome.CARDS_ONLY, None
        if reason := self.rate.day_exhausted():
            return Outcome.BUDGET_SKIPPED, reason
        return await self._fetch_card(card, run_id)

    async def _fetch_card(self, card: Card, run_id: int) -> tuple[Outcome, str | None]:
        url = self.adapter.detail_url(card.source_job_id)
        last: SoftFailure | None = None
        for attempt in range(FETCH_ATTEMPTS):
            try:
                await self._fetch_and_save(card, url, run_id)
                self.guard.record_success()
                return Outcome.FETCHED, None
            except SoftFailure as e:
                last = e
                log.info("%s %s attempt %d failed: %s", self.name, card.source_job_id, attempt + 1, e)
                if not e.retryable:
                    break
                if attempt + 1 < FETCH_ATTEMPTS:
                    await self.pacer.backoff(attempt)
        self.guard.record_soft_failure(last)  # may escalate to HardBlock
        return Outcome.FETCH_FAILED, str(last)

    async def _fetch_and_save(self, card: Card, url: str, run_id: int) -> None:
        if self.rate_limited:
            await self.rate.acquire()  # may pause until the hourly window frees up
        self.capture.reset()
        self.rate.record()
        resp = await self._navigate(self.detail, url)
        try:
            ready = await self.adapter.wait_detail_ready(self.detail)
            payloads = await self.capture.drain()
            raw = await self.adapter.snapshot(
                self.detail, job_id=card.source_job_id, requested_url=url, response=resp, ready=ready, payloads=payloads
            )
            if self.opts.screenshots:
                raw.screenshot = await self.detail.screenshot()
        except PlaywrightError as e:
            raise SoftFailure(f"snapshot failed: {e.message.splitlines()[0]}") from e
        if self.opts.resolve_apply and raw.apply_kind == "external":
            raw.apply_redirects = await apply_link.resolve(raw.apply_href, is_site_host=self.adapter.is_site_host)
        if not ready:
            log.warning("%s %s: detail-ready selector not found; saved anyway", self.name, card.source_job_id)
        path = self.store.save_posting(card, raw, batch_id=self.batch_id, search_run_id=run_id)
        self.details_fetched += 1
        log.info(
            "%s fetched %s (%s) html=%dKB payloads=%d apply=%s → %s",
            self.name,
            card.source_job_id,
            (card.title or "")[:60],
            len(raw.detail_html) // 1024,
            len(raw.payloads),
            raw.apply_kind,
            path,
        )
        await self.pacer.after_detail(self.details_fetched)

    # ---- artifacts --------------------------------------------------------------------

    async def _artifacts(self, reason: str, pages: list[Page] | None = None) -> None:
        out = self.store.failure_dir(self.batch_id, self.name)
        out.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%H%M%S")
        for tab, page in (("results", self.results), ("detail", self.detail)):
            if page is None or page.is_closed() or (pages and page not in pages):
                continue
            try:
                (out / f"{stamp}-{reason}-{tab}.png").write_bytes(await page.screenshot())
                (out / f"{stamp}-{reason}-{tab}.html").write_text(await page.content())
                (out / f"{stamp}-{reason}-{tab}.url").write_text(page.url)
            except PlaywrightError:
                pass
        log.info("%s: saved %s artifacts to %s", self.name, reason, out)
