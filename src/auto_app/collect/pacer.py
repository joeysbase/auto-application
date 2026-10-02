"""Human-like pacing, and per-site limits on how many job pages are opened.

Limits are rates, not per-run caps: past the hourly limit the collector pauses and then
continues; past the daily ceiling the remaining jobs wait for a later run.
"""

import asyncio
import logging
import random
from collections import deque
from datetime import datetime, timedelta

from auto_app.core.config import SourceLimits
from auto_app.core.models import utcnow

log = logging.getLogger(__name__)

HOUR = timedelta(hours=1)
DAY = timedelta(days=1)


class RateLimiter:
    """Rolling-window limits on job-page opens, seeded with opens from earlier runs."""

    def __init__(self, source: str, limits: SourceLimits, history: list[datetime], *, fast: bool = False):
        self.source = source
        self.per_hour = limits.max_details_per_hour
        self.per_day = limits.max_details_per_day
        self.fast = fast
        self._offset = timedelta()  # fast mode: pauses advance a virtual clock instead of sleeping
        now = self.now()
        self._opens: deque[datetime] = deque(sorted(t for t in history if now - t < DAY))
        self.pauses: list[float] = []  # seconds paused, for stats

    def now(self) -> datetime:
        return utcnow() + self._offset

    def _prune(self) -> None:
        cutoff = self.now() - DAY
        while self._opens and self._opens[0] <= cutoff:
            self._opens.popleft()

    def _in_last_hour(self) -> list[datetime]:
        cutoff = self.now() - HOUR
        return [t for t in self._opens if t > cutoff]

    def day_exhausted(self) -> str | None:
        self._prune()
        if len(self._opens) >= self.per_day:
            return f"daily ceiling {self.per_day} job pages reached"
        return None

    async def acquire(self) -> None:
        """Wait (a rate pause) until opening one more job page stays within the hourly limit."""
        while True:
            recent = self._in_last_hour()
            if len(recent) < self.per_hour:
                return
            wait = (recent[0] + HOUR - self.now()).total_seconds() + 1
            resume = (datetime.now() + timedelta(seconds=wait)).strftime("%H:%M")
            log.info("%s: rate pause — %d job pages in the last hour (limit %d); resuming at %s",
                     self.source, len(recent), self.per_hour, resume)
            self.pauses.append(wait)
            if self.fast:
                self._offset += timedelta(seconds=wait)
            else:
                await asyncio.sleep(wait)

    def record(self) -> None:
        self._opens.append(self.now())


class Pacer:
    def __init__(self, limits: SourceLimits, *, fast: bool = False, rng: random.Random | None = None):
        self.limits = limits
        self.fast = fast
        self.rng = rng or random.Random()

    async def _sleep(self, lo: float, hi: float) -> None:
        await asyncio.sleep(0 if self.fast else self.rng.uniform(lo, hi))

    async def before_navigation(self) -> None:
        await self._sleep(self.limits.min_delay_s, self.limits.max_delay_s)

    async def scroll_pause(self) -> None:
        await self._sleep(0.4, 1.3)

    async def after_detail(self, details_done: int) -> None:
        every = self.limits.long_pause_every
        if every and details_done % every == 0:
            lo, hi = self.limits.long_pause_s
            if not self.fast:
                log.info("long pause after %d details", details_done)
            await self._sleep(lo, hi)

    async def backoff(self, attempt: int) -> None:
        base = 5 * 2**attempt
        await self._sleep(base, base * 1.5)
