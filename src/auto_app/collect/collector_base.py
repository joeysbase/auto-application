"""What every collector shares, browser or API: limits, records (search runs, sightings),
source health, and the summary. Kinds differ only in how they find and fetch jobs."""

import logging
from collections import Counter
from datetime import datetime, timedelta

from auto_app.collect.guard import HardBlock, NeedsLogin
from auto_app.collect.pacer import DAY, RateLimiter
from auto_app.core.models import Card, Outcome, StopReason, utcnow
from auto_app.core.notify import notify

log = logging.getLogger(__name__)


class CollectorBase:
    def __init__(self, name: str, runner, batch_id: str, limits):
        self.name = name
        self.cfg = runner.cfg
        self.store = runner.store
        self.opts = runner.opts
        self.paths = runner.paths
        self.batch_id = batch_id
        self.limits = limits
        self.rate = RateLimiter(name, limits, self.store.fetch_times(name, utcnow() - DAY), fast=self.opts.fast)
        self.rate_limited = True
        self.pages_loaded = 0
        self.details_fetched = 0
        self.stats: Counter = Counter()
        self.stop_reasons: Counter = Counter()
        self.status = "ok"
        self.error: str | None = None
        self.manual_checks = 0

    # ---- health ---------------------------------------------------------------------

    def in_cooldown(self) -> bool:
        health = self.store.source_health(self.name)
        until = health.get("cooldown_until")
        if not self.opts.force and health.get("status") == "cooldown" and until and datetime.fromisoformat(until) > utcnow():
            log.warning("%s in cooldown until %s (%s); skipping", self.name, until, health.get("last_block_reason"))
            self.status = "skipped_cooldown"
            return True
        return False

    def mark_ok(self) -> None:
        now = utcnow()
        self.store.update_health(self.name, status="ok", last_ok_at=now, last_run_at=now, cooldown_until=None)

    def record_block(self, e: HardBlock) -> None:
        now = utcnow()
        self.error = str(e)
        if isinstance(e, NeedsLogin):
            self.status = "needs_login"
            self.store.update_health(self.name, status="needs_login", last_block_at=now, last_block_reason=str(e))
            notify("auto-app", f"{self.name}: login needed")
        else:
            self.status = "blocked"
            until = now + timedelta(hours=self.limits.cooldown_hours_on_block)
            self.store.update_health(
                self.name, status="cooldown", cooldown_until=until, last_block_at=now, last_block_reason=str(e)
            )
            notify("auto-app", f"{self.name} stopped: {e}")
        log.error("%s stopped: %s", self.name, e)

    # ---- known check -------------------------------------------------------------------

    def known(self, job_id: str) -> bool:
        """Level 1a: already saved → touch and skip."""
        if not self.opts.ignore_known and self.store.posting_exists(self.name, job_id):
            self.store.touch(self.name, job_id)
            return True
        return False

    # ---- records ----------------------------------------------------------------------

    def summary(self) -> dict:
        return {
            "source": self.name,
            "status": self.status,
            "error": self.error,
            "outcomes": {str(k): v for k, v in self.stats.items()},
            "stop_reasons": dict(self.stop_reasons),
            "pages_loaded": self.pages_loaded,
            "details_fetched": self.details_fetched,
            "rate_pauses": len(self.rate.pauses),
            "rate_paused_minutes": round(sum(self.rate.pauses) / 60, 1),
            "manual_checks": self.manual_checks,
        }

    def start_run(self, search_name: str, params: dict, url: str | None) -> int:
        return self.store.start_search_run(
            self.batch_id,
            {
                "batch_id": self.batch_id,
                "search_name": search_name,
                "source": self.name,
                "query": params,
                "search_url": url,
                "started_at": utcnow(),
                "status": "running",
            },
        )

    def finish_run(self, run_id: int, stop: StopReason, pages_loaded: int, counts: Counter) -> None:
        self.stop_reasons[stop] += 1
        self.store.finish_search_run(
            self.batch_id,
            run_id,
            {
                "source": self.name,
                "finished_at": utcnow(),
                "status": "finished",
                "stop_reason": stop,
                "pages_loaded": pages_loaded,
                "outcomes": {str(k): v for k, v in counts.items()},
            },
        )
        log.info("%s run %d finished: %s %s", self.name, run_id, stop, {str(k): v for k, v in counts.items()})

    def sighting(self, run_id: int, search_name: str, card: Card, outcome: Outcome, detail: str | None) -> None:
        record = {
            "search_run_id": run_id,
            "batch_id": self.batch_id,
            "search_name": search_name,
            **card.model_dump(),
            "outcome": outcome,
            "outcome_detail": detail,
            "seen_at": utcnow(),
        }
        self.store.add_sighting(self.batch_id, record, card.html if self.opts.card_html else None)
