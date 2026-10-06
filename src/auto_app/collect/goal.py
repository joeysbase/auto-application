"""A collect round's goal (docs/step4-run.md §2.1): when one browsing session is done.

A round ends, per source, at the first of:
- **caught up** — every search reached postings already collected (the known streak), or the end of its results (the
  natural "done"; the search loop already stops there: `early_stop` / `done`);
- **time_limit** — a cap, spread across the searches: each search may use the time left ÷ the searches left, so time a
  quick search doesn't need flows on to the next one;
- **max_new_postings** / **max_pages** — volume caps.
Per-site safety limits (Step 1's pacing, hourly / daily caps, cooldowns) always apply as well.
"""

import time
from collections import Counter
from dataclasses import dataclass, field

from auto_app.core.models import StopReason

CAUGHT_UP = {StopReason.DONE, StopReason.EARLY_STOP}  # a search that ended here has seen everything new


@dataclass
class RoundGoal:
    time_limit_s: float | None = None
    max_new_postings: int | None = None
    max_pages: int | None = None

    def budget(self, clock=time.monotonic) -> "Budget":
        return Budget(self, clock)

    @property
    def any(self) -> bool:
        return any(v is not None for v in (self.time_limit_s, self.max_new_postings, self.max_pages))


@dataclass
class Budget:
    """One source's share of a round: time, new postings, results pages."""

    goal: RoundGoal
    clock: object = time.monotonic
    started: float = 0.0
    new_postings: int = 0
    pages: int = 0
    skipped_searches: int = 0  # searches never started because the budget ran out
    hit: StopReason | None = None  # what ended this source's round early, if anything
    query_deadline: float | None = field(default=None, repr=False)

    def __post_init__(self):
        self.started = self.clock()

    @property
    def deadline(self) -> float | None:
        return self.started + self.goal.time_limit_s if self.goal.time_limit_s is not None else None

    def time_left(self) -> float | None:
        return None if self.deadline is None else self.deadline - self.clock()

    def start_query(self, queries_left: int) -> None:
        """This search's share of the time left: an even split over the searches still to run."""
        left = self.time_left()
        self.query_deadline = None if left is None else self.clock() + max(left, 0) / max(queries_left, 1)

    def exhausted(self) -> StopReason | None:
        """The whole source is done for this round (time or a volume cap)."""
        if self.deadline is not None and self.clock() >= self.deadline:
            self.hit = StopReason.TIME_LIMIT
        elif self.goal.max_new_postings is not None and self.new_postings >= self.goal.max_new_postings:
            self.hit = StopReason.GOAL
        elif self.goal.max_pages is not None and self.pages >= self.goal.max_pages:
            self.hit = StopReason.GOAL
        else:
            return None
        return self.hit

    def query_over(self) -> StopReason | None:
        """This search's share is used up (or the whole budget is)."""
        if (r := self.exhausted()) is not None:
            return r
        if self.query_deadline is not None and self.clock() >= self.query_deadline:
            return StopReason.TIME_LIMIT
        return None


def caught_up(stop_reasons: Counter, skipped: int = 0) -> bool:
    """Every search of the source ended caught up (and none was skipped)."""
    total = sum(stop_reasons.values())
    return total > 0 and skipped == 0 and all(StopReason(r) in CAUGHT_UP for r, n in stop_reasons.items() if n)
