"""`auto-app filter run` (docs/step2-select.md §4–5): which jobs each filter judges, running the agent within
budget, storing the answers, and setting each job's Step 2 state.

A (job, filter) pair is judged when the job is active and undecided, the filter's `applies_to` covers it, and
the filter hasn't judged it yet — or, with `rescore`, judged it with an older version. A failed run is
retried on the next `select`.

Per filter: recommends = its answer says the job fits and has no dealbreaker, or couldn't be read (high recall).
A job is recommended if any filter that judged it (current versions) recommends it; un_recommended when all
of them said no; undecided while a filter's run failed or is missing. Jobs you decided on are never changed.
"""

import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

from auto_app.select.filters import Filter, render
from auto_app.select.job_details import build_job_details
from auto_app.select.parse import parse_answer, verdict_text

log = logging.getLogger(__name__)


@dataclass
class SelectOptions:
    filters: list[str] | None = None  # None = every loaded filter
    jobs: list[int] | None = None  # only these jobs — judged again even if already judged (decided ones too,
    #                                but their state never changes)
    limit: int | None = None  # at most this many jobs (newest first)
    rescore: bool = False  # also re-judge pairs judged with an older filter version
    max_cost: float | None = None  # stop starting new runs once this much (USD) has been spent
    parallel: int = 3
    dry_run: bool = False
    stream: bool = False  # show each agent's text and lookups live (runs one at a time)


@dataclass
class SelectReport:
    planned: list[tuple[int, str]] = field(default_factory=list)  # (job_id, filter)
    judged: int = 0
    recommend: int = 0
    dealbreakers: int = 0  # of the "no"s, those with a dealbreaker
    no: int = 0
    unparsed: int = 0
    errors: int = 0
    skipped_budget: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost: float = 0.0
    cost_unknown: int = 0  # runs whose model has no price configured
    recommended_jobs: int = 0
    un_recommended_jobs: int = 0
    lines: list[str] = field(default_factory=list)


def applies(flt: Filter, scope: set[str]) -> bool:
    return not flt.config.applies_to or any(entry in scope for entry in flt.config.applies_to)


def plan(store, filters: list[Filter], opts: SelectOptions) -> list[tuple[int, Filter]]:
    jobs = sorted(store.jobs(), key=lambda j: (j.get("posted_at") or j.get("first_seen_at") or "", j["job_id"]),
                  reverse=True)
    scopes = store.job_scopes()
    done: dict[tuple[int, str], dict[str, str]] = {}  # (job, filter) → {version: status}
    for r in store.judgment_rows():
        done.setdefault((r["job_id"], r["filter"]), {})[r["filter_version"]] = r["status"]
    pairs, picked_jobs = [], []
    if opts.jobs is not None:
        wanted = set(opts.jobs)
        jobs = [j for j in jobs if j["job_id"] in wanted]
    for j in jobs:
        if j.get("decision") and opts.jobs is None:
            continue
        job_pairs = []
        for flt in filters:
            if not applies(flt, scopes.get(j["job_id"], set())):
                continue
            versions = done.get((j["job_id"], flt.name), {})
            status = versions.get(flt.version)
            if opts.jobs is not None or status == "error" or (status is None and (not versions or opts.rescore)):
                job_pairs.append((j["job_id"], flt))
        if job_pairs:
            if opts.limit and len(picked_jobs) >= opts.limit:
                break
            picked_jobs.append(j["job_id"])
            pairs += job_pairs
    return pairs


def decide_job(store, job_id: int, filters: list[Filter], scope: set[str]) -> str | None:
    """recommended / un_recommended from the current-version judgments, or None while undecided."""
    current = {(r["filter"], r["filter_version"]): r for r in store.judgment_rows(job_id=job_id)}
    verdicts = []
    for flt in filters:
        if not applies(flt, scope):
            continue
        r = current.get((flt.name, flt.version))
        if r is None or r["status"] == "error":
            return None  # a filter still has to judge it
        verdicts.append(r["recommend"] is not False)  # True, or couldn't read → recommend (high recall)
    if not verdicts:
        return None
    return "recommended" if any(verdicts) else "un_recommended"


def apply_decisions(store, job_ids, filters: list[Filter], ttl) -> tuple[int, int]:
    scopes = store.job_scopes()
    rec = unrec = 0
    for job_id in sorted(set(job_ids)):
        job = store.job(job_id)
        if not job or job.get("decision") or job.get("archived_at"):
            continue
        verdict = decide_job(store, job_id, filters, scopes.get(job_id, set()))
        if verdict and verdict != job.get("step2"):
            store.set_job_state(job_id, step2=verdict)
        rec += verdict == "recommended"
        unrec += verdict == "un_recommended"
    if ttl is not None:
        store.refresh_job_dates(ttl)
    return rec, unrec


class Progress:
    """What `run_select` reports while it runs; the CLI shows it (a live bar in a terminal, `[n/N]` lines otherwise).
    start / done / finish are called on the main thread, began from the worker threads."""

    def start(self, total: int, jobs: int, parallel: int) -> None:
        pass

    def began(self, job_id: int, flt: "Filter") -> None:
        pass

    def done(self, job_id: int, flt: "Filter", line: str, n: int, total: int, spent: float) -> None:
        pass

    def finish(self) -> None:
        pass


class StreamPrinter:
    """Formats live events for `filter run --stream`: the agent's text as it's written, lookups on their own lines."""

    def __init__(self, write):
        self.write = write
        self.at_line_start = True

    def _line(self, text: str) -> None:
        if not self.at_line_start:
            self.write("\n")
        self.write(text + "\n")
        self.at_line_start = True

    def header(self, job_id: int, flt: Filter, title: str, n: int | None = None, total: int | None = None) -> None:
        count = f"[{n}/{total}] " if n and total else ""
        self._line(f"\n── {count}#{job_id} {title[:70]} · {flt.name} ({flt.config.model})")

    def __call__(self, kind: str, data: dict) -> None:
        if kind == "text":
            text = data.get("text") or ""
            if text:
                self.write(text)
                self.at_line_start = text.endswith("\n")
        elif kind == "tool":
            inp = data.get("input")
            shown = inp if isinstance(inp, str) else ", ".join(f"{k}={v}" for k, v in (inp or {}).items())
            self._line(f"  → {data.get('name')}: {str(shown)[:150]}")
        elif kind == "tool_result":
            mark = "✓" if data.get("ok", True) else "✗"
            preview = " ".join(str(data.get("text") or "").split())  # one line, whatever the tool returned
            self._line(f"    {mark} {preview[:150]}")

    def end(self) -> None:
        if not self.at_line_start:
            self.write("\n")
            self.at_line_start = True


def run_select(store, cfg, filters: list[Filter], runner, opts: SelectOptions, progress: Progress | None = None,
               write=None) -> SelectReport:
    """runner(filter, prompt[, on_event]) -> RunResult: `api_runner(client)`, the claude-cli or DeepSeek runner.
    progress: told when the run starts, as each judgment starts and finishes, and at the end.
    With opts.stream, `write(text)` shows each run live (runs then go one at a time)."""
    progress = progress or Progress()
    rep = SelectReport()
    pairs = plan(store, filters, opts)
    rep.planned = [(job_id, flt.name) for job_id, flt in pairs]
    if opts.dry_run or not pairs:
        return rep
    for flt in {f.name: f for _, f in pairs}.values():
        store.save_filter_version(flt)
    lock = threading.Lock()
    spent = [0.0]

    def prepare(job_id: int, flt: Filter) -> str | None:
        parsed, locations = store.job_inputs(job_id)
        if parsed is None:
            return None
        return render(flt, build_job_details(parsed, locations, flt.config.job_details, flt.config.max_description_chars))

    prompts = [(job_id, flt, prepare(job_id, flt)) for job_id, flt in pairs]

    printer = StreamPrinter(write) if opts.stream and write else None
    titles = {j["job_id"]: j.get("title") or "" for j in store.jobs(archived=None)} if printer else {}

    todo = [(job_id, flt, prompt) for job_id, flt, prompt in prompts if prompt]
    total, started = len(todo), [0]

    def work(job_id: int, flt: Filter, prompt: str):
        with lock:
            if opts.max_cost is not None and spent[0] >= opts.max_cost:
                return job_id, flt, None
            started[0] += 1
            n = started[0]
        progress.began(job_id, flt)
        if printer:
            printer.header(job_id, flt, titles.get(job_id, ""), n, total)
            result = runner(flt, prompt, on_event=printer)
            printer.end()
        else:
            result = runner(flt, prompt)
        cost = result.cost_usd if result.cost_usd is not None else \
            cfg.select.cost(flt.config.model, result.input_tokens, result.output_tokens, result.web_searches)
        with lock:
            spent[0] += cost or 0.0
        return job_id, flt, (result, cost)

    touched = []
    workers = 1 if printer else max(1, opts.parallel)
    progress.start(total, len({j for j, _, _ in todo}), workers)
    finished = 0
    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(work, job_id, flt, prompt) for job_id, flt, prompt in todo]
            for fut in as_completed(futures):
                finished += 1
                job_id, flt, done = fut.result()
                if done is None:
                    rep.skipped_budget += 1
                    progress.done(job_id, flt, f"#{job_id:<5} {flt.name:<18} skipped: --max-cost reached",
                                  finished, total, spent[0])
                    continue
                _record(store, rep, touched, job_id, flt, done, finished, total, spent[0], progress)
    finally:
        progress.finish()
    rep.recommended_jobs, rep.un_recommended_jobs = apply_decisions(store, touched, filters, cfg.jobs.ttl_days)
    return rep


def _record(store, rep: SelectReport, touched: list, job_id: int, flt: Filter, done, n: int, total: int,
            spent: float, progress: Progress) -> None:
    """Save one finished judgment and count it."""
    result, cost = done
    parsed = parse_answer(flt, result.answer)
    store.save_judgment(job_id, flt, result, parsed, cost)  # written here, on one thread
    touched.append(job_id)
    rep.judged += 1
    rep.input_tokens += result.input_tokens
    rep.output_tokens += result.output_tokens
    if cost is None:
        rep.cost_unknown += 1
    else:
        rep.cost += cost
    if result.status == "error":
        rep.errors += 1
        verdict = f"error: {result.error}"
    elif not parsed.ok:
        rep.unparsed += 1
        verdict = f"couldn't read the answer ({parsed.error}) → recommended, shown in job browse"
    elif parsed.output.recommend:
        rep.recommend += 1
        verdict = "recommend"
    elif parsed.output.dealbreaker:
        rep.no += 1
        rep.dealbreakers += 1
        verdict = verdict_text(False, parsed.output.fit, True)
    else:
        rep.no += 1
        verdict = verdict_text(False, parsed.output.fit, parsed.output.dealbreaker)
    line = (f"#{job_id:<5} {flt.name:<18} {verdict}"
            + (f" — {parsed.output.reason[:90]}" if parsed.ok and parsed.output.reason else ""))
    rep.lines.append(line)
    progress.done(job_id, flt, line, n, total, spent)


def reparse(store, cfg, filters: list[Filter], dry_run: bool = False) -> list[str]:
    """Re-read stored answers with each filter's current parser; returns the changes."""
    changes, touched = [], []
    for flt in filters:
        for r in store.judgment_rows(filter=flt.name, with_answer=True):
            if r["status"] == "error":
                continue
            parsed = parse_answer(flt, r["answer"])
            o = parsed.output
            if (o.recommend, o.fit, o.dealbreaker, o.reason) == (r["recommend"], r["fit"], r["dealbreaker"], r["reason"] or ""):
                continue
            old = verdict_text(r["recommend"], r["fit"], r["dealbreaker"])
            new = verdict_text(o.recommend, o.fit, o.dealbreaker)
            changes.append(f"#{r['job_id']:<5} {flt.name:<18} v{r['filter_version']}: {old} → {new}")
            if not dry_run:
                store.update_parsed(r["job_id"], r["filter_version"], parsed)
                touched.append(r["job_id"])
    if not dry_run and touched:
        apply_decisions(store, touched, filters, cfg.jobs.ttl_days)
    return changes
