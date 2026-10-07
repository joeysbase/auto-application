"""`auto-app serve` (docs/step4-run.md §1–§3): the app running by itself — a simple loop.

    loop:
      wait: inside the window, not paused, no broken filter
      collect       auto-app run (collect → parse → normalize → dedup), until the round's goal (caught up / caps)
      filter batch  every job not judged yet, in one go — started right after collecting
      wait: the pause (from the end of collecting) AND the batch done

Edge cases after each batch (select/health.py): no usage left → wait for the reset and run the batch again; a job
failing `tries_per_job` times → unjudgeable; a broken filter → you're notified and the loop stops until you resume.

State lives in `data/serve.json` (pause, stop, next round, the last round's report), so `serve pause / resume / status`
and a restart work by reading and writing it. Each round's report is also appended to `data/logs/serve-rounds.jsonl`.
"""

import json
import logging
import os
import random
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from auto_app.core.duration import format_duration, parse_duration

log = logging.getLogger(__name__)

DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


# ---- config: config/serve.yaml ---------------------------------------------------------------------

class Window(BaseModel):
    model_config = ConfigDict(extra="forbid")
    days: str = "mon-sun"  # mon-fri, sat,sun, …
    start: str = "08:00"
    end: str = "23:00"

    def day_set(self) -> set[int]:
        out: set[int] = set()
        for part in self.days.lower().replace(" ", "").split(","):
            a, _, b = part.partition("-")
            ia, ib = DAYS.index(a), DAYS.index(b or a)
            out |= set(range(ia, ib + 1)) if ia <= ib else set(range(ia, 7)) | set(range(0, ib + 1))
        return out

    def contains(self, now: datetime) -> bool:
        hm = now.strftime("%H:%M")
        inside = self.start <= hm < self.end if self.start <= self.end else (hm >= self.start or hm < self.end)
        return now.weekday() in self.day_set() and inside

    def next_open(self, now: datetime) -> datetime:
        t = now.replace(second=0, microsecond=0)
        for _ in range(8 * 24 * 60 // 15):  # look ahead in 15-minute steps
            if self.contains(t):
                return t
            t += timedelta(minutes=15)
        return now + timedelta(hours=1)


class RoundCfg(BaseModel):
    model_config = ConfigDict(extra="forbid")
    time_limit: str | None = "30m"  # per source, spread across its searches
    max_new_postings: int | None = None
    max_pages: int | None = None


class SourceCfg(BaseModel):
    model_config = ConfigDict(extra="forbid")
    time_limit: str | None = None
    max_new_postings: int | None = None
    max_pages: int | None = None
    every: str | None = None  # this source at most once in this long (e.g. 6h for company career sites)
    enabled: bool = True  # false: left out of rounds (not `off:` — YAML reads the word off as false)


class FilterCfg(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workers: int | None = None  # judgments at once (default: AUTO_APP_SELECT__PARALLEL)
    tries_per_job: int = 3
    broken_after: int = 3
    usage_retry: str = "15m"  # no reset time given: try again after this


class ServeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    window: Window = Window()
    round: RoundCfg = RoundCfg()
    interval: str = "30m"  # the pause after collecting ends
    jitter: float = Field(0.2, ge=0, le=0.9)  # ± this share of the pause
    sources: dict[str, SourceCfg] = Field(default_factory=dict)
    filter: FilterCfg = FilterCfg()
    headless: bool = True  # job-site browsers hidden
    retention: str | None = "24h"  # how often to drop old raw pages, sightings and logs (None: never)

    @field_validator("interval", "retention")
    @classmethod
    def _duration(cls, v):
        if v is not None:
            parse_duration(v)
        return v


def load_serve_config(path: Path) -> ServeConfig:
    if not path.is_file():
        return ServeConfig()
    return ServeConfig.model_validate(yaml.safe_load(path.read_text()) or {})


def goal_for(cfg: ServeConfig, source: str | None = None):
    from auto_app.collect.goal import RoundGoal

    r, s = cfg.round, cfg.sources.get(source or "", SourceCfg())
    tl = s.time_limit or r.time_limit
    return RoundGoal(time_limit_s=parse_duration(tl) if tl else None,
                     max_new_postings=s.max_new_postings if s.max_new_postings is not None else r.max_new_postings,
                     max_pages=s.max_pages if s.max_pages is not None else r.max_pages)


# ---- state: data/serve.json ----------------------------------------------------------------------------

def _iso(d: datetime | None) -> str | None:
    return d.isoformat(timespec="seconds") if d else None


def _dt(s: str | None) -> datetime | None:
    return datetime.fromisoformat(s) if s else None


class State:
    """data/serve.json — read and written whole (small); `serve pause / resume` edit it while the loop runs."""

    def __init__(self, path: Path):
        self.path = path

    def read(self) -> dict:
        try:
            return json.loads(self.path.read_text())
        except (OSError, ValueError):
            return {}

    def update(self, **changes) -> dict:
        data = self.read()
        for k, v in changes.items():
            if v is None:
                data.pop(k, None)
            else:
                data[k] = v
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, default=str))
        tmp.replace(self.path)  # atomic: a reader never sees half a file
        return data


# ---- the loop ----------------------------------------------------------------------------------------

@dataclass
class Round:
    """One round's report (docs/step4-run.md §2.4)."""
    started: datetime
    collect_ended: datetime | None = None
    sources: dict = field(default_factory=dict)  # name → {new, pages, caught_up, goal_hit, status}
    new_jobs: int = 0
    judged: int = 0
    recommended: int = 0
    unjudgeable: int = 0
    filtered_at: datetime | None = None
    next_round_at: datetime | None = None
    retention: str | None = None  # what the daily retention dropped, if it ran this round
    problems: list[str] = field(default_factory=list)

    def to_json(self) -> dict:
        return {"started": _iso(self.started), "collect_ended": _iso(self.collect_ended), "sources": self.sources,
                "new_jobs": self.new_jobs, "judged": self.judged, "recommended": self.recommended,
                "unjudgeable": self.unjudgeable, "filtered_at": _iso(self.filtered_at),
                "next_round_at": _iso(self.next_round_at), "retention": self.retention, "problems": self.problems}


def format_round(r: dict) -> str:
    def hm(s):
        return s[11:16] if s else "?"
    src = " · ".join(
        f"{n}: " + ("caught up" if v.get("caught_up") else
                    f"not caught up ({v.get('stopped_by') or v.get('goal_hit') or v.get('status')})")
        + f", {v.get('new', 0)} new / {v.get('pages', 0)} pages"
        for n, v in (r.get("sources") or {}).items()) or "nothing collected"
    out = f"round {hm(r.get('started'))}–{hm(r.get('collect_ended'))} · {src}"
    out += f"\n      {r.get('new_jobs', 0)} new jobs · {r.get('judged', 0)} judged ({r.get('recommended', 0)} recommended"
    out += f", {r['unjudgeable']} unjudgeable)" if r.get("unjudgeable") else ")"
    if r.get("next_round_at"):
        out += f" · next round ≈ {hm(r['next_round_at'])}"
    if r.get("retention"):
        out += f"\n      retention: {r['retention']}"
    for p in r.get("problems") or []:
        out += f"\n      ! {p}"
    return out


class Serve:
    """The loop. collect(sources, goals) -> (batch summary, new jobs); judge() -> SelectReport (or raises
    BrokenFilters); notify(title, text); retention() -> a one-line summary (dropping old raw data, sightings, logs;
    every `cfg.retention`); clock() / sleep(s) — all injectable for tests. Expired jobs need nothing here: they're
    archived or deleted whenever job dates are refreshed (collecting and judging do that)."""

    def __init__(self, cfg: ServeConfig, state: State, collect: Callable, judge: Callable, notify: Callable,
                 store=None, rounds_log: Path | None = None, clock=datetime.now, sleep=time.sleep,
                 tick_s: float = 30, rand=random.random, retention: Callable | None = None):
        self.cfg, self.state, self.collect, self.judge, self.notify = cfg, state, collect, judge, notify
        self.retention = retention
        self.store, self.rounds_log, self.clock, self.sleep, self.tick_s, self.rand = (
            store, rounds_log, clock, sleep, tick_s, rand)

    # ---- what may run now -------------------------------------------------------------------------

    def blocked(self, now: datetime) -> str | None:
        """Why no round may start now (None: it may)."""
        st = self.state.read()
        if st.get("stopped"):
            return f"stopped: {st['stopped']} — fix it, then `auto-app serve resume`"
        if st.get("paused") or (st.get("paused_until") and _dt(st["paused_until"]) > now):
            return "paused" + (f" until {st['paused_until'][:16]}" if st.get("paused_until") else "")
        if not self.cfg.window.contains(now):
            return f"outside the window ({self.cfg.window.days} {self.cfg.window.start}–{self.cfg.window.end})"
        if st.get("next_round_at") and _dt(st["next_round_at"]) > now:
            return f"pausing until {st['next_round_at'][11:16]}"
        if st.get("filter_pending"):
            return "the last round's jobs are still being judged"
        return None

    def due_sources(self, now: datetime, all_sources: list[str]) -> list[str] | None:
        """None: every source; else those whose `every` allows a round now (and not `off`)."""
        if not self.cfg.sources:
            return None
        last = self.state.read().get("source_rounds", {})
        out = []
        for s in all_sources:
            sc = self.cfg.sources.get(s)
            if sc and not sc.enabled:
                continue
            if sc and sc.every and last.get(s) and now - _dt(last[s]) < timedelta(seconds=parse_duration(sc.every)):
                continue
            out.append(s)
        return out

    # ---- one round ----------------------------------------------------------------------------------

    def pause_length(self) -> float:
        base = parse_duration(self.cfg.interval)
        return base * (1 + self.cfg.jitter * (2 * self.rand() - 1))

    def run_round(self, all_sources: list[str]) -> Round:
        now = self.clock()
        r = Round(started=now)
        sources = self.due_sources(now, all_sources)
        self.state.update(status="collecting", round_started=_iso(now))
        goals = {s: goal_for(self.cfg, s) for s in (sources if sources is not None else all_sources)}
        try:
            batch, new_jobs = self.collect(sources, goals)
        except Exception as e:  # a crashed collect: report it, try again next round
            batch, new_jobs = {}, 0
            r.problems.append(f"collect failed: {type(e).__name__}: {e}"[:300])
        r.collect_ended = self.clock()
        r.new_jobs = new_jobs
        rounds = self.state.read().get("source_rounds", {})
        for name, s in (batch.get("sources") or {}).items():
            o = s.get("outcomes") or {}
            stopped = next((k for k in s.get("stop_reasons") or {} if k not in ("done", "early_stop")), None)
            r.sources[name] = {"new": o.get("fetched", 0), "pages": s.get("pages_loaded", 0),
                               "caught_up": s.get("caught_up"), "goal_hit": s.get("goal_hit"), "status": s.get("status"),
                               "stopped_by": s.get("goal_hit") or stopped}  # e.g. a search's own max_pages
            rounds[name] = _iso(r.started)
            if s.get("status") in ("needs_login", "blocked"):
                r.problems.append(f"{name}: {s.get('status')} — {s.get('error') or ''}"[:300])
        r.next_round_at = r.collect_ended + timedelta(seconds=self.pause_length())  # the pause: from collecting's end
        self.state.update(status="filtering", source_rounds=rounds, next_round_at=_iso(r.next_round_at),
                          filter_pending=True)
        self.filter_batch(r)
        return r

    def filter_batch(self, r: Round) -> None:
        """Judge every job not judged yet; then the edge cases. Repeats after a usage reset; stops on a broken filter."""
        from auto_app.select.health import assess

        retries = 0
        while True:
            st = self.state.read()
            if st.get("usage_until") and _dt(st["usage_until"]) > self.clock():
                self.state.update(status="waiting for usage")
                self._sleep_until(_dt(st["usage_until"]))
                continue
            try:
                rep = self.judge()
            except BrokenFilters as e:
                self._stop(r, "; ".join(f"filter {n}: {why}" for n, why in e.broken.items()))
                return
            except Exception as e:  # the batch itself crashed: tell you, stop until you've looked
                self._stop(r, f"filter batch failed: {type(e).__name__}: {e}"[:400])
                return
            now = self.clock()
            h = assess(rep, now, tries_per_job=self.cfg.filter.tries_per_job, broken_after=self.cfg.filter.broken_after,
                       retry_s=parse_duration(self.cfg.filter.usage_retry), store=self.store)
            r.judged += rep.judged - len(rep.failures)
            r.recommended += rep.recommended_jobs
            r.unjudgeable += len(h.unjudgeable)
            if h.broken:
                self._stop(r, "; ".join(f"filter {n} is broken: {why}" for n, why in h.broken.items()))
                return
            if h.usage_until:  # nobody's fault: wait for the reset, then judge what's left
                self.state.update(usage_until=_iso(h.usage_until), status="waiting for usage")
                self.notify("auto-app: usage used up",
                            f"judging waits until about {h.usage_until:%H:%M} ({h.usage_error[:80]}); collecting waits too")
                continue
            if h.retrying and retries < self.cfg.filter.tries_per_job:  # a job failed: another try in this batch
                retries += 1
                continue
            break
        r.filtered_at = self.clock()
        self.state.update(filter_pending=None, usage_until=None)
        self.retain(r)

    def retain(self, r: Round) -> None:
        """The daily retention, after a round's batch (every `cfg.retention`)."""
        if self.retention is None or self.cfg.retention is None:
            return
        last, now = _dt(self.state.read().get("retention_at")), self.clock()
        if last and now - last < timedelta(seconds=parse_duration(self.cfg.retention)):
            return
        try:
            r.retention = self.retention()
        except Exception as e:  # not worth stopping for: try again next round
            r.problems.append(f"retention failed: {type(e).__name__}: {e}"[:300])
            return
        self.state.update(retention_at=_iso(now))

    def _stop(self, r: Round, why: str) -> None:
        r.problems.append(why)
        self.state.update(stopped=why[:500], filter_pending=None, status="stopped")
        self.notify("auto-app: collecting stopped", why[:200])

    def _sleep_until(self, until: datetime) -> None:
        while (left := (until - self.clock()).total_seconds()) > 0:
            self.sleep(min(left, self.tick_s))

    def finish(self, r: Round) -> None:
        rep = r.to_json()
        self.state.update(last_round=rep, status="pausing" if not self.state.read().get("stopped") else "stopped")
        if self.rounds_log:
            self.rounds_log.parent.mkdir(parents=True, exist_ok=True)
            with self.rounds_log.open("a") as f:
                f.write(json.dumps(rep) + "\n")
        log.info(format_round(rep))
        if r.recommended:
            self.notify("auto-app", f"{r.recommended} new recommendation(s) — ask Claude Code (or `auto-app ui`)")
        for p in r.problems:
            if not p.startswith("filter"):  # (a broken filter was already told)
                self.notify("auto-app", p[:200])

    # ---- forever ------------------------------------------------------------------------------------

    def backlog_due(self) -> bool:
        """A batch left unjudged (`serve resume` after a stop, or a restart mid-batch), not paused or stopped."""
        st = self.state.read()
        paused = st.get("paused") or (st.get("paused_until") and _dt(st["paused_until"]) > self.clock())
        return bool(st.get("filter_pending")) and not st.get("stopped") and not paused

    def step(self, all_sources: list[str]) -> Round | None:
        """One tick: a round if one may start now, else None."""
        if self.blocked(self.clock()) is not None:
            return None
        r = self.run_round(all_sources)
        self.finish(r)
        return r

    def run_forever(self, all_sources: list[str], once: bool = False) -> None:
        self.state.update(pid=os.getpid(), started=_iso(self.clock()), status="idle")
        while True:
            if self.backlog_due():  # at start-up, or `serve resume` while this loop waits: judge it first
                self.state.update(status="filtering")
                self.filter_batch(Round(started=self.clock()))
            if once and (why := self.blocked(self.clock())) is not None:
                self.state.update(status=why)
                raise NotNow(why)  # `serve run --once`: no round may start now
            r = self.step(all_sources)
            if once and r is not None:
                return
            if r is None:
                why = self.blocked(self.clock())
                self.state.update(status=why or "idle")
                self.sleep(self.tick_s)


class NotNow(Exception):
    """`--once` when no round may start now (the reason)."""


class BrokenFilters(Exception):
    def __init__(self, broken: dict[str, str]):
        super().__init__("; ".join(broken))
        self.broken = broken


# ---- staying awake --------------------------------------------------------------------------------------

def keep_awake() -> subprocess.Popen | None:
    """macOS: no idle sleep while `serve` runs (caffeinate -i, tied to this process)."""
    try:
        return subprocess.Popen(["caffeinate", "-i", "-w", str(os.getpid())], stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL)
    except OSError:
        return None


def describe(cfg: ServeConfig, st: dict) -> str:
    lines = [f"status       {st.get('status', 'not running')}" + (f" (pid {st['pid']})" if st.get("pid") else "")]
    if st.get("stopped"):
        lines.append(f"stopped      {st['stopped']}  → fix it, then `auto-app serve resume`")
    if st.get("paused") or st.get("paused_until"):
        lines.append("paused       " + (f"until {st['paused_until'][:16]}" if st.get("paused_until") else "until you resume"))
    if st.get("usage_until"):
        lines.append(f"usage        used up — judging resumes about {st['usage_until'][11:16]}")
    if st.get("next_round_at"):
        lines.append(f"next round   ≈ {st['next_round_at'][:16].replace('T', ' ')}"
                     + (" (after the jobs are judged)" if st.get("filter_pending") else ""))
    w = cfg.window
    lines.append(f"schedule     rounds until caught up (cap {cfg.round.time_limit or 'none'}), "
                 f"then {format_duration(parse_duration(cfg.interval))} ± {int(cfg.jitter * 100)}% · "
                 f"window {w.days} {w.start}–{w.end}")
    lines.append("cleanup      expired jobs: archived / deleted as their date passes · old raw data, sightings, logs: "
                 + (f"every {cfg.retention}" + (f" (last {st['retention_at'][:16].replace('T', ' ')})"
                                                if st.get("retention_at") else "") if cfg.retention else "never"))
    if st.get("last_round"):
        lines += ["", format_round(st["last_round"])]
    return "\n".join(lines)


# ---- launchd: `auto-app serve install` (KeepAlive) ------------------------------------------------------

LABEL = "com.auto-app.serve"


def build_plist(uv: str | None = None, project: Path | None = None) -> dict:
    import shutil

    from auto_app.schedule import service_path
    from auto_app.settings import PROJECT_ROOT

    project = project or PROJECT_ROOT
    uv = uv or shutil.which("uv") or "/opt/homebrew/bin/uv"
    out = project / "data" / "logs" / "serve.log"
    return {
        "Label": LABEL,
        "ProgramArguments": [uv, "run", "--project", str(project), "auto-app", "serve", "run"],
        "WorkingDirectory": str(project),
        "RunAtLoad": True,
        "KeepAlive": True,  # restarted after a crash, a reboot or a log-in
        "ThrottleInterval": 60,
        "StandardOutPath": str(out),
        "StandardErrorPath": str(out),
        "EnvironmentVariables": {"PATH": service_path()},
        "ProcessType": "Background",
    }


def plist_path() -> Path:
    from auto_app.schedule import AGENTS

    return AGENTS / f"{LABEL}.plist"


def install(plist: dict) -> str:
    import plistlib

    from auto_app.schedule import AGENTS, _launchctl

    AGENTS.mkdir(parents=True, exist_ok=True)
    Path(plist["StandardOutPath"]).parent.mkdir(parents=True, exist_ok=True)
    plist_path().write_bytes(plistlib.dumps(plist))
    domain = f"gui/{os.getuid()}"
    _launchctl("bootout", f"{domain}/{LABEL}")
    r = _launchctl("bootstrap", domain, str(plist_path()))
    if r.returncode != 0:
        raise RuntimeError(f"launchctl bootstrap failed: {r.stderr.strip() or r.stdout.strip()}")
    return str(plist_path())


def remove() -> bool:
    from auto_app.schedule import _launchctl

    _launchctl("bootout", f"gui/{os.getuid()}/{LABEL}")
    if plist_path().exists():
        plist_path().unlink()
        return True
    return False
