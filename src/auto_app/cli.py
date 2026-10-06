import asyncio
import json
import logging
import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Annotated, Optional

import typer
from playwright.async_api import async_playwright

from auto_app.collect.browser import BrowserSession, close_chrome, open_plain_chrome
from auto_app.collect.detect import detect as detect_platform
from auto_app.collect.plan import ConfigError, Selection, build_tasks, format_plan
from auto_app.collect.report import format_batch, format_one, format_table
from auto_app.collect.runner import CollectionRunner, CollectOptions
from auto_app.collect.store import FileStore, PostingRef
from auto_app.core.config import GENERIC_FIELDS, AppConfig, load_config, load_env_file
from auto_app.core.logging import setup_logging
from auto_app.core.models import utcnow
from auto_app.settings import PROJECT_ROOT, get_paths
from auto_app.sources import SOURCES, get_adapter
from auto_app.sources.filters import describe
from auto_app.sources.registry import default_registry

app = typer.Typer(no_args_is_help=True, add_completion=False, help="A personal job-application pipeline. Step 1: collect jobs (source, collect, process, run). Step 2: judge them with your filters (filter). Step 3: apply (apply). The pool: job. Storage: data.")
schedule_app = typer.Typer(no_args_is_help=True, help="Run `auto-app run` automatically (macOS launchd).")
source_app = typer.Typer(no_args_is_help=True, help="Step 1 setup: sources (one plugin each), logins, health, what collect would run.")
collect_app = typer.Typer(no_args_is_help=True, help="Step 1a: run the searches and save raw postings; fetch one job page; batch stats.")
process_app = typer.Typer(no_args_is_help=True, help="Step 1b: parse → normalize → dedup postings into jobs.")
job_app = typer.Typer(no_args_is_help=True, help="The job pool: list jobs, show one, set a job's state by hand.")
filter_app = typer.Typer(no_args_is_help=True, help="Step 2: your filters — list and check them, run them on jobs, see why a job was judged so.")
data_app = typer.Typer(no_args_is_help=True, help="Storage: inspect postings, export / import files, purge (job TTL + retention).")
apply_app = typer.Typer(no_args_is_help=True, help="Step 3: apply to the jobs you queued — your profile and answers, the apply policy, the queue, the inbox.")
app.add_typer(schedule_app, name="schedule")
app.add_typer(source_app, name="source")
app.add_typer(collect_app, name="collect")
app.add_typer(process_app, name="process")
app.add_typer(job_app, name="job")
app.add_typer(filter_app, name="filter")
app.add_typer(data_app, name="data")
app.add_typer(apply_app, name="apply")

OutOpt = Annotated[Optional[Path], typer.Option("--db", help="Database file (default: <project>/data/auto_app.db)")]
ConfigOpt = Annotated[Optional[Path], typer.Option("--config", help="searches.yaml (default: <project>/config/searches.yaml)")]


_debug_files = False


@app.callback()
def main(
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False,
    debug_files: Annotated[
        bool, typer.Option("--debug-files", help="Write postings/batches as files under data/raw/ instead of the database")
    ] = False,
):
    global _debug_files
    setup_logging(verbose)
    load_env_file(get_paths().home / ".env")  # global settings + ANTHROPIC_API_KEY; the shell's variables win
    _debug_files = debug_files


def _store(db: Path | None = None, cfg: AppConfig | None = None):
    """The database store — or, with --debug-files (or AUTO_APP_DEBUG_FILES=true in .env), the file store
    under data/raw/ *instead* (nothing is written to the database; `import-files` loads it later)."""
    from auto_app.core.db.store import DbStore

    paths = get_paths()
    if cfg is None:
        try:
            cfg = load_config(paths.config_file if paths.config_file.exists() else None)
        except Exception:  # config problems are reported by the commands that need the config
            cfg = None
    if _debug_files or (cfg is not None and cfg.debug_files):
        return FileStore(paths.raw)
    return DbStore(db or paths.db, paths)


def _require_db(store, what: str) -> None:
    if not hasattr(store, "create_job"):
        typer.echo(f"{what} needs the database; this command is running with debug files only "
                   f"({store.describe()}). Run it without --debug-files (and `debug_files: false`), "
                   f"or load the files first with `auto-app data import`.", err=True)
        raise typer.Exit(2)


def _config(path: Path | None) -> AppConfig:
    path = path or get_paths().config_file
    if not path.exists():
        example = PROJECT_ROOT / "config" / "searches.example.yaml"
        typer.echo(f"config not found: {path}\ncopy the example:  cp {example} {path}", err=True)
        raise typer.Exit(2)
    return load_config(path)


def _config_or_env(path: Path | None) -> AppConfig:
    """The config when there is one (searches.yaml + .env); otherwise just the .env / environment settings."""
    if path or get_paths().config_file.exists():
        return _config(path)
    return load_config(None)


def _check_source(source: str) -> None:
    """`login` works for browser sources that need a signed-in profile."""
    reg = default_registry()
    usable = sorted(n for n, i in reg.plugins.items() if i.kind == "browser" and getattr(i.cls, "needs_login", True))
    if source not in usable:
        raise typer.BadParameter(f"expected one of {', '.join(usable)}")


@source_app.command("login")
def login(
    source: str,
    verify: Annotated[bool, typer.Option(help="Check the saved session afterwards (headless)")] = True,
    config: ConfigOpt = None,
):
    """Open a normal (non-automated) Chrome on SOURCE's profile so you can log in by hand."""
    _check_source(source)
    cfg = _config_or_env(config)
    adapter = get_adapter(source, cfg.limits(source).base_url)
    profile = get_paths().profile(source)

    proc = open_plain_chrome(profile, adapter.spec.url(adapter.spec.login_url))
    typer.echo(
        f"A separate Chrome window opened for {source} (profile: {profile}).\n"
        "1. Log in there (Google / school SSO work). Finish any verification step until the site shows you signed in.\n"
        "2. Then quit that Chrome with Cmd+Q (Chrome ▸ Quit Google Chrome) while its window is in front.\n"
        "   Quitting is what saves the login. Your normal Chrome is a separate app and stays open.\n"
        "Waiting for that Chrome to quit…"
    )
    try:
        proc.wait()
    except KeyboardInterrupt:
        close_chrome(proc)
        typer.echo("\ninterrupted; the login may not have been saved", err=True)
        raise typer.Exit(1)

    store = _store(None)
    if not verify:
        return

    async def _verify():
        async with async_playwright() as pw:
            async with BrowserSession(pw, profile, headless=True) as bs:
                await bs.results.goto(adapter.spec.url(adapter.spec.session_check_url))
                return await adapter.check_session(bs.results), bs.results.url

    status, landed = asyncio.run(_verify())
    typer.echo(f"{source} session: {status}  (checked {landed})")
    if status == "needs_login":
        typer.echo("still logged out — run `auto-app source login` again and make sure the site shows you as signed in", err=True)
        raise typer.Exit(1)
    if store.source_health(source).get("status") == "needs_login":
        store.update_health(source, status="ok")


def _selection(source, company, job_sites_only, companies_only, search) -> Selection:
    names = [*(source or []), *(company or [])] or None
    return Selection(sources=names, search_names=search, job_sites=not companies_only, companies=not job_sites_only)


def _tasks(cfg: AppConfig, sel: Selection):
    try:
        return build_tasks(cfg, default_registry(), sel)
    except ConfigError as e:
        typer.echo(f"config problem:\n{e}", err=True)
        raise typer.Exit(2)


SiteOpt = Annotated[Optional[list[str]], typer.Option("--source", "-s", help="Source(s), by plugin name (repeatable)")]
JobSitesOnly = Annotated[bool, typer.Option("--job-sites", help="Only job sites (sources with many employers)")]
CompaniesOnly = Annotated[bool, typer.Option("--companies", help="Only company sources (one employer each)")]
SearchOpt = Annotated[Optional[list[str]], typer.Option("--search", help="Search name(s) from config; default: all")]


@collect_app.command("run")
def collect(
    site: SiteOpt = None,
    job_sites_only: JobSitesOnly = False,
    companies_only: CompaniesOnly = False,
    search: SearchOpt = None,
    cards_only: Annotated[bool, typer.Option(help="Read cards and record sightings only; fetch no job pages")] = False,
    ignore_known: Annotated[bool, typer.Option(help="Re-fetch jobs that were already saved")] = False,
    max_pages: Annotated[Optional[int], typer.Option(help="Cap result pages per job-site search")] = None,
    interactive: Annotated[bool, typer.Option(help="Pause after opening each search to adjust filters by hand")] = False,
    capture_all_json: Annotated[bool, typer.Option(help="Save every JSON response from the site (for spikes)")] = False,
    screenshots: Annotated[bool, typer.Option(help="Save a screenshot per job page")] = False,
    card_html: Annotated[bool, typer.Option(help="Keep each search card's HTML (debugging card reading)")] = False,
    retry: Annotated[bool, typer.Option(help="Retry previously failed fetches first")] = True,
    headless: bool = False,
    force: Annotated[bool, typer.Option(help="Ignore source cooldowns")] = False,
    manual_checks: Annotated[
        bool, typer.Option(help="When a site shows a security check, wait for you to complete it in the window")
    ] = False,
    scheduled: Annotated[bool, typer.Option(help="Enforce run window and minimum interval (for cron)")] = False,
    config: ConfigOpt = None,
    out: OutOpt = None,
):
    """Run each source's searches and save raw job data."""
    cfg = _config(config)
    store = _store(out, cfg)
    if manual_checks and headless:
        raise typer.BadParameter("--manual-checks needs a visible browser; drop --headless")
    sel = _selection(site, None, job_sites_only, companies_only, search)
    tasks = _tasks(cfg, sel)
    if scheduled and not _apply_schedule(cfg, store, sel, tasks):
        raise typer.Exit(0)
    typer.echo(format_plan(tasks))
    opts = CollectOptions(
        headless=headless,
        cards_only=cards_only,
        ignore_known=ignore_known,
        max_pages=max_pages,
        screenshots=screenshots,
        card_html=card_html,
        interactive=interactive,
        capture_all_json=capture_all_json,
        retry=retry,
        force=force,
        manual_checks=manual_checks,
    )
    try:
        batch = asyncio.run(CollectionRunner(cfg, get_paths(), store, opts).run(sel))
    except KeyboardInterrupt:
        typer.echo("\nstopped — every job saved so far is kept; the next run continues from there.\n")
        typer.echo(format_batch(store, None))
        raise typer.Exit(130)
    typer.echo("")
    typer.echo(format_batch(store, batch["batch_id"]))
    typer.echo(f"\ninspect with:  auto-app data inspect{' --db ' + str(out) if out else ''}")


def _apply_schedule(cfg: AppConfig, store, sel: Selection, tasks) -> bool:
    """--scheduled: keep only sources allowed to run now (run window, minimum interval). False = nothing to run."""
    keep = set(_scheduled_sources(cfg, store, [t.source for t in tasks]))
    if not keep:
        return False
    sel.sources = [t.source for t in tasks if t.source in keep]
    return True


@app.command()
def run(
    site: SiteOpt = None,
    job_sites_only: JobSitesOnly = False,
    companies_only: CompaniesOnly = False,
    search: SearchOpt = None,
    collect_: Annotated[bool, typer.Option("--collect/--no-collect", help="Collect first (off: only process saved postings)")] = True,
    do_purge: Annotated[bool, typer.Option("--purge", help="Also expire jobs and apply retention (auto-app data purge) at the end")] = False,
    max_pages: Annotated[Optional[int], typer.Option(help="Cap result pages per job-site search")] = None,
    headless: bool = False,
    force: Annotated[bool, typer.Option(help="Ignore source cooldowns")] = False,
    manual_checks: Annotated[bool, typer.Option(help="Wait for you to complete security checks in the window")] = False,
    scheduled: Annotated[bool, typer.Option(help="Enforce run window and minimum interval; notify when done (for launchd)")] = False,
    time_limit: Annotated[Optional[str], typer.Option("--time-limit", help="The round's time cap per source, spread across its searches (e.g. 30m, 1h); stops early once caught up")] = None,
    max_new: Annotated[Optional[int], typer.Option("--max-new", help="The round's cap on new postings per source")] = None,
    round_pages: Annotated[Optional[int], typer.Option("--round-pages", help="The round's cap on result pages per source")] = None,
    config: ConfigOpt = None,
    out: OutOpt = None,
):
    """The whole of Step 1: collect → parse → normalize → dedup (→ purge). A round ends when every search is caught up
    (it reached postings already collected), or at --time-limit / --max-new / --round-pages."""
    from auto_app.core.notify import notify
    from auto_app.pipeline import RunLocked, format_pipeline, run_lock, run_pipeline

    cfg = _config(config)
    store = _store(out, cfg)
    sel = _selection(site, None, job_sites_only, companies_only, search)
    tasks = _tasks(cfg, sel)
    do_collect = collect_
    if scheduled and do_collect and not _apply_schedule(cfg, store, sel, tasks):
        do_collect = False  # nothing may collect right now; still process what's saved
    from auto_app.collect.goal import RoundGoal
    from auto_app.core.duration import parse_duration

    try:
        goal = RoundGoal(time_limit_s=parse_duration(time_limit) if time_limit else None, max_new_postings=max_new,
                         max_pages=round_pages)
    except ValueError as e:
        raise typer.BadParameter(str(e), param_hint="--time-limit")
    opts = CollectOptions(headless=headless, max_pages=max_pages, force=force, manual_checks=manual_checks,
                          goal=goal if goal.any else None)
    try:
        with run_lock(get_paths().home / "data" / "run.lock"):
            res = asyncio.run(run_pipeline(cfg, get_paths(), store, opts, sel, default_registry(),
                                           collect=do_collect, do_purge=do_purge))
    except RunLocked as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(3)
    except KeyboardInterrupt:
        typer.echo("\nstopped — everything saved so far is kept; the next run continues from there.")
        raise typer.Exit(130)
    typer.echo(format_pipeline(res, store))
    if scheduled:
        msg = f"{res.new_jobs} new jobs" + (f" · {len(res.problems)} problems" if res.problems else "")
        notify("auto-app run", msg)


@data_app.command("purge")
def purge_cmd(
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Show what would be removed; remove nothing")] = False,
    vacuum: Annotated[bool, typer.Option(help="Compact the database file afterwards")] = False,
    config: ConfigOpt = None,
    out: OutOpt = None,
):
    """Expire jobs (job TTL from .env: delete, or archive recommended ones), then drop old raw data, sightings and logs."""
    from auto_app.core.db.purge import purge

    cfg = _config_or_env(config)
    store = _store(out, cfg)
    _require_db(store, "`purge`")
    typer.echo(purge(store, cfg.retention, ttl=cfg.jobs.ttl_days, dry_run=dry_run, vacuum=vacuum).format())





@app.command("ui")
def ui(
    directory: Annotated[Optional[Path], typer.Option("--dir", help="Filters folder (default: <project>/filters)")] = None,
    config: ConfigOpt = None,
    out: OutOpt = None,
):
    """Open the full-screen app: Jobs (F1), Audit (F2), Inbox (F3) — switch with the function keys."""
    from auto_app.select.filters import FILTERS_DIR, load_filters

    cfg = _config_or_env(config)
    store = _store(out, cfg)
    _require_db(store, "`ui`")
    _check_themes(cfg)
    if not sys.stdout.isatty():
        typer.echo("`ui` is a full-screen app — run it in a terminal", err=True)
        raise typer.Exit(2)
    _run_ui(cfg, store, load_filters(directory or FILTERS_DIR), "jobs")


@schedule_app.command("install")
def schedule_install(
    at: Annotated[list[str], typer.Option("--at", help="Time of day HH:MM (repeatable)")] = ["09:00"],
    headless: Annotated[bool, typer.Option(help="Run browsers without windows (default: visible, safest for LinkedIn)")] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Print the launchd plist; install nothing")] = False,
):
    """Install (or replace) a daily schedule: `auto-app run --scheduled --purge` at the given times."""
    import plistlib

    from auto_app import schedule

    plist = schedule.build_plist(at, headless=headless)
    if dry_run:
        typer.echo(plistlib.dumps(plist).decode())
        return
    path = schedule.install(plist)
    typer.echo(f"installed {path}\n{schedule.show()}")


@schedule_app.command("show")
def schedule_show():
    """Show the installed schedule."""
    from auto_app import schedule

    typer.echo(schedule.show())


@schedule_app.command("remove")
def schedule_remove():
    """Remove the schedule."""
    from auto_app import schedule

    typer.echo("schedule removed" if schedule.remove() else "no schedule was installed")


def _scheduled_sources(cfg: AppConfig, store: FileStore, sources: list[str], now: datetime | None = None) -> list[str]:
    """For cron runs: only inside the run window, and only sources not run in the last N hours."""
    now = now or datetime.now()
    start, end = cfg.run_window.start, cfg.run_window.end
    if not (start <= now.strftime("%H:%M") <= end):
        typer.echo(f"outside run window {start}-{end}; not running")
        return []
    keep = []
    for s in sources:
        last = store.source_health(s).get("last_run_at")
        if last and utcnow() - datetime.fromisoformat(last) < timedelta(hours=cfg.min_hours_between_runs):
            typer.echo(f"{s}: last run {last} is within {cfg.min_hours_between_runs} h; skipping")
        else:
            keep.append(s)
    return keep


@process_app.command("parse")
def parse(
    site: SiteOpt = None,
    reparse: Annotated[bool, typer.Option(help="Parse again even if already parsed with the current parser")] = False,
    config: ConfigOpt = None,
    out: OutOpt = None,
):
    """Turn saved raw postings into common fields (parsed.json) and show field coverage per source."""
    from auto_app.process.parse import format_coverage, parse_postings

    cfg = _config_or_env(config)
    sources = list(site or []) or None
    stats = parse_postings(_store(out, cfg), cfg, default_registry(), sources, reparse=reparse)
    typer.echo(format_coverage(stats) if stats else "no saved postings")


@process_app.command("normalize")
def normalize(
    site: SiteOpt = None,
    again: Annotated[bool, typer.Option("--again", help="Normalize again even if up to date")] = False,
    config: ConfigOpt = None,
    out: OutOpt = None,
):
    """Compute each posting's dedup keys (normalized.json): company, title, employer job key, description hash."""
    from auto_app.process.normalize import format_normalize, normalize_postings

    cfg = _config_or_env(config)
    store = _store(out, cfg)
    sources = list(site or []) or None
    stats = normalize_postings(store, default_registry(), sources, again=again)
    typer.echo(format_normalize(stats) if stats else "no saved postings")


@process_app.command("dedup")
def dedup(
    preview: Annotated[bool, typer.Option("--preview", help="Show what dedup would do; write nothing")] = False,
    rebuild: Annotated[bool, typer.Option(help="Unlink everything and regroup from scratch (only while no job is evaluated, decided or archived)")] = False,
    near_misses: Annotated[bool, typer.Option(help="List pairs that looked related but weren't merged")] = True,
    limit: Annotated[int, typer.Option(help="Max jobs / near misses to print")] = 50,
    config: ConfigOpt = None,
    out: OutOpt = None,
):
    """Link new normalized postings to jobs (Level 1b same source, Level 2 across sources)."""
    from auto_app.process.link import format_link_plan, link_jobs

    cfg = _config_or_env(config)
    store = _store(out, cfg)
    if not preview:
        _require_db(store, "writing jobs (`dedup` without --preview)")
    if rebuild:
        if preview:
            raise typer.BadParameter("use --rebuild without --preview (a preview never writes anyway)")
        advanced = [j for j in store.jobs(archived=None) if j["state"] != "not_evaluated"]
        if advanced:
            typer.echo(f"refusing to rebuild: {len(advanced)} jobs are already evaluated, decided or archived "
                       f"(e.g. job #{advanced[0]['job_id']} is {advanced[0]['state']}); rebuilding would lose that", err=True)
            raise typer.Exit(2)
        typer.echo(f"removed {store.clear_jobs()} jobs; regrouping all postings")
    plan, created = link_jobs(store, default_registry(), dry_run=preview, ttl=cfg.jobs.ttl_days)
    typer.echo(format_link_plan(plan, created, dry_run=preview, limit=limit, show_near=near_misses))
    if not preview:
        typer.echo(f"jobs now: {len(store.jobs())} · list them with `auto-app job list`")


STATES = ("not_evaluated", "un_recommended", "recommended", "want_to_apply", "applied", "not_applied")


def _jobs(
    job_id: Annotated[Optional[int], typer.Argument(help="Show one job: its details, postings and matching keys")] = None,
    source: Annotated[Optional[list[str]], typer.Option("--source", "-s", help="Only jobs found on this source (repeatable)")] = None,
    company: Annotated[Optional[str], typer.Option("--company", "-c", help="Only jobs whose company contains this text")] = None,
    state: Annotated[Optional[str], typer.Option(
        help="Only jobs in this state: not_evaluated | un_recommended | recommended (undecided) | want_to_apply | applied | not_applied")] = None,
    archived: Annotated[bool, typer.Option("--archived", help="List archived job records instead of active jobs")] = False,
    limit: Annotated[Optional[int], typer.Option(help="Show at most N jobs (newest first)")] = None,
    out: OutOpt = None,
):
    """List jobs (deduplicated openings), newest first, or show one job in detail."""
    from auto_app.process.jobs_view import _newest_first, format_job, format_job_list

    store = _store(out)
    _require_db(store, "`jobs`")
    if job_id is not None:
        j = store.job(job_id)
        if not j:
            typer.echo(f"no job #{job_id}", err=True)
            raise typer.Exit(1)
        text = format_job(j, store.job_postings(job_id), store.job_keys_of(job_id))
        if j.get("archived_at") and j.get("decision") == "applied":
            desc = store.archived_description(job_id)
            text += f"\n\ndescription (kept with the record, {len(desc)} chars):\n{desc}" if desc else ""
        typer.echo(text)
        return
    if state and state not in STATES:
        raise typer.BadParameter(f"expected one of {', '.join(STATES)}", param_hint="--state")
    rows = store.jobs(archived=archived)
    if not rows:
        typer.echo("no archived jobs" if archived else "no jobs yet — run `auto-app process dedup`")
        return
    if state:
        rows = [j for j in rows if (j.get("decision") or j.get("step2") or "not_evaluated") == state
                and (state != "recommended" or not j.get("decision"))]
    if source:
        rows = [j for j in rows if set(source) & set(j["sources"])]
    if company:
        rows = [j for j in rows if company.lower() in (j.get("company") or j.get("company_norm") or "").lower()]
    if not rows:
        typer.echo("no jobs match those filters")
        return
    if limit:
        rows = sorted(rows, key=_newest_first, reverse=True)[:limit]
    typer.echo(format_job_list(rows))


def _check_themes(cfg: AppConfig) -> None:
    from textual.theme import BUILTIN_THEMES

    from auto_app.process.job_browser import check_themes

    bad = check_themes(cfg.ui)
    if bad:
        typer.echo(f"unknown theme {', '.join(bad)} in AUTO_APP_UI__… — use auto or one of: "
                   f"{', '.join(sorted(BUILTIN_THEMES))}", err=True)
        raise typer.Exit(2)


@job_app.command("browse")
def job_browse(
    source: Annotated[Optional[list[str]], typer.Option("--source", "-s", help="Only jobs found on this source (repeatable; `s` in the app)")] = None,
    company: Annotated[Optional[str], typer.Option("--company", "-c", help="Only jobs whose company contains this text (`c` in the app)")] = None,
    state: Annotated[Optional[str], typer.Option(
        help="Open on this tab: recommended (new recommended, default) | all | not_evaluated | all_recommended | un_recommended | applied | not_applied")] = None,
    filter_: Annotated[Optional[list[str]], typer.Option("--filter", "-f", help="Only jobs these filters judged, and only their verdicts (repeatable)")] = None,
    directory: Annotated[Optional[Path], typer.Option("--dir", help="Filters folder (default: <project>/filters)")] = None,
    config: ConfigOpt = None,
    out: OutOpt = None,
):
    """Browse and review jobs in a full-screen app: tabs per state, details, filter verdicts; decide, rate, set states."""
    from auto_app.process.job_browser import TABS
    from auto_app.select.filters import FILTERS_DIR, load_filters

    if state and state not in TABS:
        raise typer.BadParameter(f"expected one of {', '.join(TABS)}", param_hint="--state")
    cfg = _config_or_env(config)
    store = _store(out, cfg)
    _require_db(store, "`job browse`")
    _check_themes(cfg)
    fs = load_filters(directory or FILTERS_DIR)  # for "older version" marks; judgments show even if a filter is gone
    _known_filters(store, fs, filter_)
    if not sys.stdout.isatty():
        typer.echo("`job browse` is a full-screen app — run it in a terminal (or use `job list`)", err=True)
        raise typer.Exit(2)
    _run_ui(cfg, store, fs, "jobs", jobs=_browser(store, cfg, fs, filter_, source=source, company=company),
            jobs_tab=state or "recommended")


def _browser(store, cfg: AppConfig, fs, only: list[str] | None, source=None, company=None):
    from auto_app.process.job_browser import JobBrowser

    return JobBrowser(store, list(fs.filters.values()), ttl=cfg.jobs.ttl_days, sources=source, company=company,
                      only_filters=only, noise_limit=cfg.select.noise_limit, audit_more=cfg.select.audit_more)


def _run_ui(cfg: AppConfig, store, fs, start: str, jobs=None, audit=None, jobs_tab: str = "recommended") -> None:
    """Open `auto-app ui` on one section; the others get their defaults (the inbox, if the apply policy loads)."""
    from auto_app.apply.profile import load_me
    from auto_app.ui import AutoApp

    why = {}
    try:
        inbox = _applications(cfg, store)
    except typer.Exit:
        inbox, why["inbox"] = None, "The apply policy (config/apply.yaml) doesn't load — run `auto-app apply policy`."
    AutoApp(jobs=jobs or _browser(store, cfg, fs, None), audit=audit or _browser(store, cfg, fs, None), inbox=inbox,
            cv=load_me(get_paths().home).cv, start=start, jobs_tab=jobs_tab, ui=cfg.ui, why=why,
            automated=cfg.apply.automated).run()


def _known_filters(store, fs, names: list[str] | None) -> None:
    known = {r["filter"] for r in store.judgment_rows()}
    unknown = [n for n in names or [] if n not in known and n not in fs.filters]
    if unknown:
        typer.echo(f"no filter named {unknown} has judged any job (filters: {', '.join(sorted(known)) or 'none'})", err=True)
        raise typer.Exit(2)


@job_app.command("mark")
def mark(
    job_ids: Annotated[list[int], typer.Argument(help="Job number(s)", metavar="JOB_ID...")],
    to: Annotated[str, typer.Option("--as", help="recommended | un_recommended | want_to_apply | applied | not_applied | not_evaluated")],
    config: ConfigOpt = None,
    out: OutOpt = None,
):
    """Set jobs' state by hand (until Steps 2–3 do it): Step 2's verdict and/or your decision."""
    cfg = _config_or_env(config)
    store = _store(out, cfg)
    _require_db(store, "`mark`")
    if to not in STATES:
        raise typer.BadParameter(f"expected one of {', '.join(STATES)}", param_hint="--as")
    for job_id in job_ids:
        try:
            store.mark_job(job_id, to)
        except ValueError as e:
            typer.echo(str(e), err=True)
            raise typer.Exit(1)
    store.refresh_job_dates(cfg.jobs.ttl_days)
    for job_id in job_ids:
        j = store.job(job_id)
        typer.echo(f"job #{job_id}: {j['state']}  (expires {(j.get('expires_at') or '—')[:10]})  {j.get('title') or ''}")


def _filters(
    name: Annotated[Optional[str], typer.Argument(help="Check one filter and show its settings")] = None,
    job: Annotated[Optional[int], typer.Option("--job", help="With NAME: print exactly what the agent gets for this job")] = None,
    directory: Annotated[Optional[Path], typer.Option("--dir", help="Filters folder (default: <project>/filters)")] = None,
    config: ConfigOpt = None,
    out: OutOpt = None,
):
    """Step 2 filters (one folder each: command.md, filter.yaml, optional parser.py): list, check, preview."""
    from auto_app.select.filters import EXAMPLES_DIR, FILTERS_DIR, check_applies_to, load_filters, render
    from auto_app.select.job_details import build_job_details

    folder = directory or FILTERS_DIR
    fs = load_filters(folder)
    cfg = _config_or_env(config)
    reg = default_registry()

    def problems(f):
        return check_applies_to(f, cfg, reg)

    if not name:
        if not fs.filters and not fs.broken:
            typer.echo(f"no filters in {_rel(str(folder))}. Start from an example:\n"
                       f"  mkdir -p {_rel(str(FILTERS_DIR))} && cp -r {_rel(str(EXAMPLES_DIR))}/ml-engineer {_rel(str(FILTERS_DIR))}/")
            return
        for f in fs.filters.values():
            reads = "parser.py" if f.parser else "output convention"
            scope = ", ".join(f.config.applies_to) if f.config.applies_to else "every job"
            typer.echo(f"{f.name:<20} v{f.version}  {f.config.model}  tools {f.config.tools or 'none'}  reads: {reads}  "
                       f"judges: {scope}")
            if f.description:
                typer.echo(f"{'':<20} {f.description}")
            for msg in problems(f):
                typer.echo(f"{'':<20} ✗ {msg}")
            for w in f.warnings:
                typer.echo(f"{'':<20} ! {w}")
        for broken, why in fs.broken.items():
            typer.echo(f"{broken:<20} ✗ not loaded: {why}")
        return

    try:
        f = fs.get(name)
    except KeyError as e:
        typer.echo(e.args[0], err=True)
        raise typer.Exit(1)
    errs = problems(f)
    if job is None:
        c = f.config
        typer.echo(f"{f.name} — v{f.version}  ({_rel(str(f.path))})")
        if f.description:
            typer.echo(f.description)
        typer.echo(f"  job details   {', '.join(c.job_details)}  (description cut at {c.max_description_chars} chars)")
        typer.echo(f"  agent         {c.model}; tools {c.tools or 'none'}; at most {c.max_turns} turns, {c.max_tool_calls} tool calls")
        typer.echo(f"  reads answer  {'parser.py' if f.parser else 'the output convention: {\"recommend\": true|false, \"reason\": …}'}")
        typer.echo(f"  judges        {', '.join(c.applies_to) if c.applies_to else 'every job'}")
        typer.echo(f"  other files   {', '.join(f.files) or 'none'}  (the agent may read these)")
        for msg in errs:
            typer.echo(f"  ✗ {msg}")
        for w in f.warnings:
            typer.echo(f"  ! {w}")
        typer.echo(f"\npreview what the agent gets:  auto-app filter show {f.name} --job <#>")
        raise typer.Exit(1 if errs else 0)
    store = _store(out, cfg)
    _require_db(store, "`filters --job`")
    parsed, locations = store.job_inputs(job)
    if parsed is None:
        typer.echo(f"job #{job}: not found, archived, or its primary posting isn't parsed", err=True)
        raise typer.Exit(1)
    typer.echo(render(f, build_job_details(parsed, locations, f.config.job_details, f.config.max_description_chars)))


def _load_filters_for(names: list[str] | None, directory: Path | None, cfg: AppConfig):
    from auto_app.select.filters import FILTERS_DIR, check_applies_to, load_filters

    fs = load_filters(directory or FILTERS_DIR)
    if names:
        try:
            chosen = [fs.get(n) for n in names]
        except KeyError as e:
            typer.echo(e.args[0], err=True)
            raise typer.Exit(2)
    else:
        chosen = list(fs.filters.values())
        for name, why in fs.broken.items():
            typer.echo(f"! filter {name} not loaded: {why}", err=True)
    if not chosen:
        typer.echo("no filters — create one: mkdir -p filters && cp -r filters.example/ml-engineer filters/", err=True)
        raise typer.Exit(2)
    errors = [f"{f.name}: {m}" for f in chosen for m in check_applies_to(f, cfg, default_registry())]
    if errors:
        typer.echo("filter problems:\n  " + "\n  ".join(errors), err=True)
        raise typer.Exit(2)
    return chosen


def _select(
    filter_: Annotated[Optional[list[str]], typer.Option("--filter", "-f", help="Only these filters (repeatable); default: all")] = None,
    limit: Annotated[Optional[int], typer.Option(help="At most N jobs (newest first)")] = None,
    rescore: Annotated[bool, typer.Option(help="Also re-judge jobs judged by an older version of a filter")] = False,
    max_cost: Annotated[Optional[float], typer.Option("--max-cost", help="Stop starting new runs after this many USD")] = None,
    parallel: Annotated[Optional[int], typer.Option(help="Agent runs at once (default: AUTO_APP_SELECT__PARALLEL)")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Show what would be judged (or re-read); run nothing")] = False,
    reparse: Annotated[bool, typer.Option(help="Re-read stored answers with the current parsers (no agent runs, no cost)")] = False,
    directory: Annotated[Optional[Path], typer.Option("--dir", help="Filters folder (default: <project>/filters)")] = None,
    config: ConfigOpt = None,
    out: OutOpt = None,
    jobs: list[int] | None = None,
    stream: bool = False,
):
    """Step 2: run your filters' agents on active, undecided jobs and mark them recommended / un_recommended."""
    from auto_app.select.engine import SelectOptions, reparse as do_reparse, run_select

    cfg = _config_or_env(config)
    store = _store(out, cfg)
    _require_db(store, "`select`")
    chosen = _load_filters_for(filter_, directory, cfg)
    if reparse:
        changes = do_reparse(store, cfg, chosen, dry_run=dry_run)
        typer.echo("\n".join(changes) if changes else "no answers read differently with the current parsers")
        if changes:
            typer.echo(f"\n{len(changes)} judgments {'would change' if dry_run else 'changed'}")
        return
    opts = SelectOptions(filters=filter_, jobs=jobs, limit=limit, rescore=rescore, max_cost=max_cost,
                         parallel=parallel or cfg.select.parallel, dry_run=dry_run, stream=stream)
    if stream and not dry_run and (parallel or cfg.select.parallel) > 1:
        typer.echo("--stream: running one job at a time so the output stays readable")
    if jobs:
        from auto_app.select.engine import applies

        scopes = store.job_scopes()
        for job_id in jobs:
            j = store.job(job_id)
            if not j:
                typer.echo(f"job #{job_id}: no such job", err=True)
            elif j.get("archived_at"):
                typer.echo(f"job #{job_id}: archived — not judged", err=True)
            else:
                skipped = [f.name for f in chosen if not applies(f, scopes.get(job_id, set()))]
                if skipped:
                    typer.echo(f"job #{job_id}: not in the applies_to of {skipped}", err=True)
                if j.get("decision"):
                    typer.echo(f"job #{job_id}: you already decided ({j['decision']}) — judged for `filter why`, "
                               f"its state won't change", err=True)
    from auto_app.select.runner_deepseek import is_deepseek
    from auto_app.select.setup import SetupError, make_runner, runner_kind

    deepseek_filters = [f for f in chosen if is_deepseek(f.config.model)]
    claude_filters = [f for f in chosen if not is_deepseek(f.config.model)]
    kind = runner_kind(cfg)
    if max_cost is not None:
        priced = claude_filters if kind == "api" else []
        unpriced = sorted({f.config.model for f in priced + deepseek_filters} - set(cfg.select.prices))
        if unpriced:
            typer.echo(f"--max-cost needs a price for {unpriced}: add it to AUTO_APP_SELECT__PRICES in .env "
                       f"(Claude: anthropic.com/pricing; DeepSeek: api-docs.deepseek.com)", err=True)
            raise typer.Exit(2)
    for f in deepseek_filters:
        if "web_search" in f.config.tools:
            typer.echo(f"note: {f.name} uses {f.config.model} — web_search isn't available with DeepSeek, "
                       f"so it runs with web_fetch / read_file only", err=True)
    runner = None
    if not dry_run:
        try:
            runner = make_runner(cfg, chosen, say=typer.echo)
        except SetupError as e:
            typer.echo(str(e), err=True)
            raise typer.Exit(2)
    if jobs and hasattr(store, "engine"):  # judged again: a fresh start (tries, unjudgeable)
        from auto_app.select.health import clear

        clear(store, jobs)

    from auto_app.select.progress_view import make_progress

    progress = None if dry_run else make_progress(stream, typer.echo)
    rep = run_select(store, cfg, chosen, runner, opts, progress=progress,
                     write=lambda t: (sys.stdout.write(t), sys.stdout.flush()))
    jobs = len({j for j, _ in rep.planned})
    if not rep.planned:
        typer.echo("nothing to judge" + (" for those jobs" if jobs else
                   ": every active, undecided job has been judged by the current filters"
                   + ("" if rescore else " (use --rescore to redo older versions)")))
        return
    if dry_run:
        typer.echo(f"would run {len(rep.planned)} judgments on {jobs} jobs:")
        for job_id, name in rep.planned[:30]:
            typer.echo(f"  #{job_id:<5} {name}")
        if len(rep.planned) > 30:
            typer.echo(f"  … and {len(rep.planned) - 30} more")
        return
    cost = f"${rep.cost:.2f}" + (f" + {rep.cost_unknown} runs without a configured price" if rep.cost_unknown else "")
    if runner is not None and kind == "claude-cli" and claude_filters:
        cost += " (Claude Code: list-price equivalent, billed to your subscription)"
    no = f"{rep.no} no" + (f" ({rep.dealbreakers} for a dealbreaker)" if rep.dealbreakers else "")
    typer.echo(f"\njudged {rep.judged}: {rep.recommend} recommend, {no}, {rep.unparsed} couldn't read, "
               f"{rep.errors} errors (retried next time)"
               + (f"; {rep.skipped_budget} not run (--max-cost reached)" if rep.skipped_budget else ""))
    typer.echo(f"jobs now: {rep.recommended_jobs} recommended, {rep.un_recommended_jobs} un_recommended "
               f"(of those judged this run) · tokens {rep.input_tokens:,} in / {rep.output_tokens:,} out · cost {cost}")
    typer.echo("review them: auto-app job browse · details of one job: auto-app filter why <#>")


@filter_app.command("why")
def why(
    job_id: Annotated[int, typer.Argument(help="Job number")],
    trace: Annotated[bool, typer.Option(help="Show every model turn and tool result")] = False,
    directory: Annotated[Optional[Path], typer.Option("--dir", help="Filters folder (default: <project>/filters)")] = None,
    out: OutOpt = None,
):
    """Step 2: why a job was (or wasn't) recommended — each filter's answer, how it was read, what it looked up."""
    from auto_app.select.parse import verdict_text
    from auto_app.select.filters import FILTERS_DIR, load_filters

    store = _store(out)
    _require_db(store, "`why`")
    j = store.job(job_id)
    if not j:
        typer.echo(f"no job #{job_id}", err=True)
        raise typer.Exit(1)
    current = {f.name: f.version for f in load_filters(directory or FILTERS_DIR).filters.values()}
    typer.echo(f"job #{job_id}  {j.get('title') or ''} — {j.get('company') or j.get('company_norm') or ''}")
    typer.echo(f"state: {j['state']}" + (f" (Step 2: {j['step2']})" if j.get("step2") else ""))
    rows = store.judgment_rows(job_id=job_id, with_answer=True)
    if not rows:
        typer.echo("not judged yet — run `auto-app filter run`")
        return
    for r in rows:
        ver = r["filter_version"]
        when = ("current" if current.get(r["filter"]) == ver else
                "out of date" if r["filter"] in current else "filter no longer present")
        verdict = verdict_text(r["recommend"], r["fit"], r["dealbreaker"]) if r["status"] != "error" else "error"
        if r["status"] != "error" and r["fit"] is not None:
            verdict += f"   (fit: {'yes' if r['fit'] else 'no'} · dealbreaker: " \
                       f"{'yes' if r['dealbreaker'] else 'no' if r['dealbreaker'] is False else 'not given'})"
        cost = f"${r['cost_usd']:.4f}" if r["cost_usd"] is not None else "cost unknown"
        typer.echo(f"\n── {r['filter']}  v{ver} ({when}) · {r['model']} · {r['created_at'][:16]}")
        typer.echo(f"   verdict   {verdict}" + (f" — {r['reason']}" if r["reason"] else ""))
        if r["parse_error"]:
            typer.echo(f"   reading   {r['parser']}: {r['parse_error']}")
        if r["error"]:
            typer.echo(f"   run error {r['error']}")
        typer.echo(f"   usage     {r['input_tokens'] or 0:,} in / {r['output_tokens'] or 0:,} out tokens · "
                   f"{r['tool_calls'] or 0} tool calls · {cost}")
        lookups = [b for t in r["trace"] for b in t.get("blocks", []) if b.get("type") in ("server_tool_use", "tool_use")]
        for b in lookups:
            typer.echo(f"   looked up {b.get('name')}: {json.dumps(b.get('input'), ensure_ascii=False)[:150]}")
        if trace:
            for t in r["trace"]:
                typer.echo(f"   turn {t.get('turn')} (stop: {t.get('stop')})")
                for b in t.get("blocks", []):
                    typer.echo(f"     {json.dumps(b, ensure_ascii=False, default=str)[:400]}")
        typer.echo("   answer:")
        for line in (r["answer"] or "(none)").splitlines():
            typer.echo(f"     {line}")


@source_app.command("plan")
def plan(
    site: SiteOpt = None,
    job_sites_only: JobSitesOnly = False,
    companies_only: CompaniesOnly = False,
    search: SearchOpt = None,
    config: ConfigOpt = None,
):
    """Show what `collect run` would run, and where each search field is applied (native / local / deferred)."""
    cfg = _config(config)
    typer.echo(format_plan(_tasks(cfg, _selection(site, None, job_sites_only, companies_only, search))))


def _plugins(name: Annotated[Optional[str], typer.Argument(help="Show one plugin (or platform) in detail")] = None):
    """List sources (one plugin each: built-in job sites and plugins/), or show one's search options."""
    from auto_app.platforms import PLATFORMS

    reg = default_registry()
    if not name:
        for info in reg.plugins.values():
            what = f"company {info.company} ({info.platform})" if info.is_company else f"job site ({info.kind})"
            typer.echo(f"{info.name:<14} {what:<36} {_rel(info.origin)}")
        typer.echo(f"\nplatforms (bases for company plugins): {', '.join(PLATFORMS)}  — add one with `auto-app source add URL`")
        for where, why in reg.broken.items():
            typer.echo(f"! not loaded: {_rel(where)} — {why}", err=True)
        return
    if name in reg.plugins:
        info = reg.get(name)
        cls = info.cls
        what = f"company source: {info.company}, {info.platform} board {info.board}" if info.is_company else "job site"
        typer.echo(f"{info.name} — {what} ({info.kind} plugin, {_rel(info.origin)})")
    elif name in PLATFORMS:
        cls = PLATFORMS[name]
        typer.echo(f"{name} — platform (a base for company plugins; `auto-app source add URL` creates one)")
    else:
        try:
            reg.get(name)
        except KeyError as e:
            typer.echo(e.args[0], err=True)
        raise typer.Exit(1)
    doc = (cls.__doc__ or "").strip() or (sys.modules[cls.__module__].__doc__ or "").strip()
    if doc:
        typer.echo(doc)
    if getattr(cls, "kind", "") == "api":
        if getattr(cls, "platform", None):
            typer.echo(f"board: {cls.board_help}")
        typer.echo(f"fetches job detail separately: {'yes' if cls.needs_detail else 'no'}")
    typer.echo("search fields:")
    for f in GENERIC_FIELDS:
        typer.echo(f"  {f:<20} {describe(cls.filters.get(f))}")
    fields = cls.SiteFilters.model_fields
    typer.echo("site_filters:" + ("" if fields else " none"))
    for fname, fi in fields.items():
        typer.echo(f"  {fname:<20} {fi.annotation}  (default {fi.default!r})")
    sf_doc = (cls.SiteFilters.__doc__ or "").strip()
    if fields and sf_doc:
        typer.echo("  " + sf_doc.replace("\n", "\n  "))


def _rel(path: str) -> str:
    try:
        return str(Path(path).relative_to(PROJECT_ROOT))
    except ValueError:
        return path


@source_app.command("add")
def add_company(
    url: Annotated[str, typer.Argument(help="Company careers page, or its job-board URL")],
    name: Annotated[Optional[str], typer.Option("--name", "--key", help="Source name (default: from the board)")] = None,
    company: Annotated[Optional[str], typer.Option(help="Employer's display name (default: from the name)")] = None,
    config: ConfigOpt = None,
):
    """Detect a company's job platform, write its plugin (plugins/companies/NAME.py) and a search stub in the config."""
    from auto_app.platforms import PLATFORMS

    path = config or get_paths().config_file
    cfg = _config(path)
    found, final_url = asyncio.run(detect_platform(url))
    found = [(p, b) for p, b in found if p in PLATFORMS]
    if not found:
        typer.echo(
            f"no known job platform found at {final_url}.\n"
            "If you know the company's job-board URL (e.g. https://<tenant>.wd5.myworkdayjobs.com/<site>,\n"
            "jobs.ashbyhq.com/<name>, boards.greenhouse.io/<name>, jobs.lever.co/<name>), pass that instead.\n"
            "Otherwise the company needs its own plugin (see docs/step1-collect.md §2.6).",
            err=True,
        )
        raise typer.Exit(1)
    platform, board = found[0]
    base = PLATFORMS[platform]
    plugin = base()
    board = plugin.validate_board(board)
    name = name or re.sub(r"[^a-z0-9_-]", "", board.rstrip("/").split("/")[-1 if platform != "workday" else 2].split(".")[0].lower())
    company = company or name.replace("-", " ").replace("_", " ").title()
    reg = default_registry()
    file = PROJECT_ROOT / "plugins" / "companies" / f"{name.replace('-', '_')}.py"
    if name in reg.plugins or file.exists():
        where = _rel(reg.plugins[name].origin) if name in reg.plugins else _rel(str(file))
        typer.echo(f"a source named {name!r} already exists ({where}); pick another with --name", err=True)
        raise typer.Exit(1)
    count = asyncio.run(_count_jobs(plugin, board))
    typer.echo(f"{name}: {company} — {platform}, board {board} — {count} open jobs found")
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(company_plugin_source(name, company, platform, board))
    typer.echo(f"wrote {_rel(str(file))}  (the source: platform, board, display name — commit it with your code)")
    if name not in cfg.sources:
        _append_source_stub(path, name, cfg)
        typer.echo(f"added a search stub under `sources: {name}:` in {path} — fill in keywords / filters "
                   f"(options: `auto-app source show {name}`), then check it with `auto-app source plan -s {name}`")


def company_plugin_source(name: str, company: str, platform: str, board: str) -> str:
    from auto_app.platforms import PLATFORMS

    base = PLATFORMS[platform]
    cls = "".join(w[:1].upper() + w[1:] for w in re.split(r"[^A-Za-z0-9]+", company)) or "Company"
    if cls[0].isdigit():
        cls = "C" + cls
    q = json.dumps  # double-quoted, escaped Python string literals
    return (
        f'"""{company} careers ({platform}). Written by `auto-app source add`; edit freely —\n'
        f'override methods here for anything specific to this company\'s board."""\n\n'
        f"from {base.__module__} import {base.__name__}\n\n\n"
        f"class {cls}({base.__name__}):\n"
        f"    name = {q(name)}\n"
        f"    company = {q(company)}\n"
        f"    board = {q(board)}\n"
    )


async def _count_jobs(plugin, board: str) -> int:
    import httpx

    from auto_app.collect.api_collector import USER_AGENT, HttpContext
    from auto_app.core.search import ResolvedSearch
    from auto_app.core.config import CardFilters

    probe_search = ResolvedSearch(name="probe", keywords=[], locations=[], posted_within_days=None, workplace_types=[],
                                  employment_types=[], experience_levels=[], max_pages=1, known_streak=1,
                                  card_filters=CardFilters(), site_filters=plugin.SiteFilters())
    async with httpx.AsyncClient(timeout=30, follow_redirects=True,
                                 headers={"User-Agent": USER_AGENT, "Accept": "application/json"}) as client:
        ctx = HttpContext(client, plugin.requests_per_second)
        if plugin.platform == "workday":  # one page is enough to confirm it works; report the site's total
            data = await ctx.post_json(plugin._api(board) + "/jobs",
                                       {"appliedFacets": {}, "limit": 1, "offset": 0, "searchText": ""})
            return int(data.get("total") or 0)
        return len(await plugin.search(ctx, board, probe_search))


def _append_source_stub(path: Path, name: str, cfg: AppConfig) -> None:
    """A commented starter search under `sources:` (the source runs once a search is uncommented)."""
    first = next(((n, s.search_name) for n, sc in cfg.sources.items() for s in sc.searches), None)
    copy = f"from: {first[0]}/{first[1]}" if first else "name: main"
    entry = (f"  {name}:\n"
             f"    searches: []        # nothing runs until you add a search, e.g.:\n"
             f"    # searches:\n"
             f"    #   - {copy}      # copy that search's keywords, locations, dates …\n"
             f"    #     site_filters: {{}}   # this board's own options: `auto-app source show {name}`\n")
    text = path.read_text()
    lines = text.splitlines(keepends=True)
    for i, line in enumerate(lines):
        if re.match(r"^sources:\s*(\{\s*\})?\s*(#.*)?$", line):
            lines[i] = "sources:\n" if "{" in line else line
            j = i + 1  # after the last line of the block (indented, blank or comment lines)
            while j < len(lines) and (lines[j].startswith((" ", "\t", "#")) or not lines[j].strip()):
                j += 1
            while j > i + 1 and not lines[j - 1].strip():
                j -= 1
            lines.insert(j, entry)
            path.write_text("".join(lines))
            return
    path.write_text(text.rstrip("\n") + "\n\nsources:\n" + entry)


@collect_app.command("probe")
def probe(
    source: str,
    job: Annotated[str, typer.Argument(help="Job id or job URL")],
    screenshots: bool = True,
    capture_all_json: bool = False,
    config: ConfigOpt = None,
    out: OutOpt = None,
):
    """Fetch a single job page (browser sources) and show what was captured."""
    cfg = _config_or_env(config)
    store = _store(out)
    try:
        info = default_registry().get(source)
    except KeyError as e:
        raise typer.BadParameter(e.args[0])
    if info.kind != "browser":
        raise typer.BadParameter(f"{source} is collected through an API; use `auto-app collect run -s {source}` instead")
    adapter = default_registry().create(source, cfg.limits(source, info.cls).base_url)
    job_id = adapter.job_id_from_url(job) if "/" in job else job
    if not job_id:
        raise typer.BadParameter(f"could not find a {source} job id in {job}")
    opts = CollectOptions(ignore_known=True, screenshots=screenshots, capture_all_json=capture_all_json, force=True)
    summary = asyncio.run(CollectionRunner(cfg, get_paths(), store, opts).probe(source, job_id))
    if summary["status"] != "ok" or not store.posting_exists(source, job_id):
        typer.echo(f"probe failed: {summary}", err=True)
        raise typer.Exit(1)
    typer.echo(format_one(store, PostingRef(source, job_id)))


@data_app.command("inspect")
def inspect(
    source: Annotated[Optional[str], typer.Argument()] = None,
    job_id: Annotated[Optional[str], typer.Argument()] = None,
    chars: Annotated[int, typer.Option(help="Visible-text characters to print")] = 1200,
    out: OutOpt = None,
):
    """Summarize saved postings (all, one source, or one job)."""
    store = _store(out)
    if source and job_id:
        if not store.posting_exists(source, job_id):
            typer.echo(f"not found: {source} {job_id} in {store.describe()}", err=True)
            raise typer.Exit(1)
        typer.echo(format_one(store, PostingRef(source, job_id), chars))
    else:
        typer.echo(format_table(store, source))


@data_app.command("import")
def import_files(
    source_dir: Annotated[Optional[Path], typer.Option("--from", help="File store to import (default: data/raw)")] = None,
    out: OutOpt = None,
):
    """Load a file store (data/raw/) into the database: postings, raw data, parse/normalize results, batches."""
    from auto_app.core.db.importer import import_file_store

    store = _store(out)
    _require_db(store, "`import-files`")
    src = FileStore(source_dir or get_paths().raw)
    report = import_file_store(src, store)
    typer.echo(report)


@data_app.command("export")
def export(
    source: str,
    job_id: str,
    to: Annotated[Optional[Path], typer.Option("--to", help="Folder to write into (default: data/export)")] = None,
    out: OutOpt = None,
):
    """Write one posting from the database as files (raw page/record, parsed.json, normalized.json)."""
    from auto_app.core.db.importer import export_posting

    store = _store(out)
    _require_db(store, "`export`")
    if not store.posting_exists(source, job_id):
        typer.echo(f"not found: {source} {job_id}", err=True)
        raise typer.Exit(1)
    dest = FileStore(to or get_paths().home / "data" / "export")
    typer.echo(f"written to {export_posting(store, PostingRef(source, job_id), dest)}")


@collect_app.command("stats")
def stats(batch: Annotated[Optional[str], typer.Argument(help="Batch id; default: latest")] = None, out: OutOpt = None):
    """Show search runs and outcomes for a batch."""
    typer.echo(format_batch(_store(out), batch))


@source_app.command("health")
def sources(
    reset: Annotated[Optional[str], typer.Option(help="Clear cooldown / login status for a source")] = None,
    out: OutOpt = None,
):
    """Show per-source health (ok / needs_login / cooldown)."""
    store = _store(out)
    if reset:
        store.update_health(reset, status="ok", cooldown_until=None)
    health = store.health()
    cfg = _config_or_env(None)
    names = list(dict.fromkeys([*(n for n, sc in cfg.sources.items() if sc.searches), *health]))
    for s in names or SOURCES:
        h = health.get(s, {"status": "ok"})
        extra = f" until {h['cooldown_until']}" if h.get("cooldown_until") else ""
        reason = f" — {h['last_block_reason']}" if h.get("last_block_reason") and h["status"] != "ok" else ""
        typer.echo(f"{s:<10} {h['status']}{extra}{reason}  (last ok: {h.get('last_ok_at', '-')})")



# ---- split commands (one subcommand each) ----------------------------------------------------------

@job_app.command("list")
def job_list(
    source: Annotated[Optional[list[str]], typer.Option("--source", "-s", help="Only jobs found on this source (repeatable)")] = None,
    company: Annotated[Optional[str], typer.Option("--company", "-c", help="Only jobs whose company contains this text")] = None,
    state: Annotated[Optional[str], typer.Option(
        help="Only jobs in this state: not_evaluated | un_recommended | recommended (undecided) | want_to_apply | applied | not_applied")] = None,
    archived: Annotated[bool, typer.Option("--archived", help="List archived job records instead of active jobs")] = False,
    limit: Annotated[Optional[int], typer.Option(help="Show at most N jobs (newest first)")] = None,
    out: OutOpt = None,
):
    """List jobs (deduplicated openings), newest first."""
    _jobs(None, source, company, state, archived, limit, out)


@job_app.command("show")
def job_show(
    job_id: Annotated[int, typer.Argument(help="Job number")],
    description: Annotated[bool, typer.Option("--description", "-d", help="Also print the job's full description")] = False,
    out: OutOpt = None,
):
    """One job: details, state and expiry, its postings and how they matched, its matching keys."""
    _jobs(job_id, None, None, None, False, None, out)
    if description:
        store = _store(out)
        parsed, _ = store.job_inputs(job_id)
        text, where = (parsed or {}).get("description_text") or "", "the primary posting"
        if not text and (store.job(job_id) or {}).get("archived_at"):
            text, where = store.archived_description(job_id), "the archived record"
        if text:
            typer.echo(f"\ndescription ({len(text):,} chars, from {where}):\n")
            from auto_app.select.job_details import join_bullets

            typer.echo(join_bullets(text).strip())
        else:
            typer.echo("\nno description saved for this job (archived jobs keep it only if you applied)")


@source_app.command("list")
def source_list():
    """Sources — one plugin each: built-in job sites and plugins/ — and the platforms for company plugins."""
    _plugins(None)


@source_app.command("show")
def source_show(name: Annotated[str, typer.Argument(help="A source (or a platform such as workday)")]):
    """One source's details: company and board, supported search fields, site_filters."""
    _plugins(name)


@filter_app.command("list")
def filter_list(
    directory: Annotated[Optional[Path], typer.Option("--dir", help="Filters folder (default: <project>/filters)")] = None,
    config: ConfigOpt = None,
):
    """Your filters (one folder each): version, model, tools, how answers are read, problems and warnings."""
    _filters(None, None, directory, config, None)


@filter_app.command("show")
def filter_show(
    name: Annotated[str, typer.Argument(help="Filter name (its folder)")],
    job: Annotated[Optional[int], typer.Option("--job", help="Print exactly what the agent gets for this job")] = None,
    directory: Annotated[Optional[Path], typer.Option("--dir", help="Filters folder (default: <project>/filters)")] = None,
    config: ConfigOpt = None,
    out: OutOpt = None,
):
    """Check one filter and show its settings; with --job, the exact text its agent gets for that job."""
    _filters(name, job, directory, config, out)


@filter_app.command("run")
def filter_run(
    filter_: Annotated[Optional[list[str]], typer.Option("--filter", "-f", help="Only these filters (repeatable); default: all")] = None,
    job: Annotated[Optional[list[int]], typer.Option("--job", "-j", help="Only these jobs (repeatable), judged again even if already judged")] = None,
    limit: Annotated[Optional[int], typer.Option(help="At most N jobs (newest first)")] = None,
    rescore: Annotated[bool, typer.Option(help="Also re-judge jobs judged by an older version of a filter")] = False,
    max_cost: Annotated[Optional[float], typer.Option("--max-cost", help="Stop starting new runs after this many USD")] = None,
    parallel: Annotated[Optional[int], typer.Option(help="Agent runs at once (default: AUTO_APP_SELECT__PARALLEL)")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Show what would be judged; run nothing")] = False,
    stream: Annotated[bool, typer.Option("--stream", help="Show each agent's text and lookups live (runs one job at a time)")] = False,
    directory: Annotated[Optional[Path], typer.Option("--dir", help="Filters folder (default: <project>/filters)")] = None,
    config: ConfigOpt = None,
    out: OutOpt = None,
):
    """Run your filters' agents on active, undecided jobs (or the --job ones) and mark them recommended / un_recommended."""
    _select(filter_, limit, rescore, max_cost, parallel, dry_run, False, directory, config, out, jobs=job, stream=stream)


@filter_app.command("reparse")
def filter_reparse(
    filter_: Annotated[Optional[list[str]], typer.Option("--filter", "-f", help="Only these filters (repeatable); default: all")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Only list what would change")] = False,
    directory: Annotated[Optional[Path], typer.Option("--dir", help="Filters folder (default: <project>/filters)")] = None,
    config: ConfigOpt = None,
    out: OutOpt = None,
):
    """Re-read stored answers with the current parsers (e.g. after adding a parser.py) — no agent runs, no cost."""
    _select(filter_, None, False, None, None, dry_run, True, directory, config, out)


@filter_app.command("audit")
def filter_audit(
    filter_: Annotated[Optional[list[str]], typer.Option("--filter", "-f", help="Only these filters (repeatable); default: every filter that has judged a job")] = None,
    size: Annotated[Optional[int], typer.Option("-n", help="Jobs to draw per filter (default AUTO_APP_SELECT__AUDIT_SIZE, 30)")] = None,
    directory: Annotated[Optional[Path], typer.Option("--dir", help="Filters folder (default: <project>/filters)")] = None,
    config: ConfigOpt = None,
    out: OutOpt = None,
):
    """Label random samples of each filter's "no" jobs, with the live evaluation panel — measures how often it wrongly rejects."""
    from auto_app.select import audit
    from auto_app.select.filters import FILTERS_DIR, load_filters

    cfg = _config_or_env(config)
    store = _store(out, cfg)
    _require_db(store, "`filter audit`")
    _check_themes(cfg)
    if not sys.stdout.isatty():
        typer.echo("`filter audit` is a full-screen app — run it in a terminal (numbers: `filter eval`)", err=True)
        raise typer.Exit(2)
    fs = load_filters(directory or FILTERS_DIR)
    _known_filters(store, fs, filter_)
    browser = _browser(store, cfg, fs, filter_)
    for name in browser.audit_filters():  # a new sample only when the last one is fully labelled
        if audit.unlabeled_count(store, name) == 0:
            flt = fs.filters.get(name)
            d = audit.draw(store, name, size or cfg.select.audit_size, flt.version if flt else None)
            typer.echo(f"{name}: drew {len(d.jobs)} of {d.pool_size} \"no\" jobs" if d else f"{name}: no unlabelled \"no\" jobs to draw")
    _run_ui(cfg, store, fs, "audit", audit=browser)


@filter_app.command("eval")
def filter_eval(
    filter_: Annotated[Optional[list[str]], typer.Option("--filter", "-f", help="Only these filters (repeatable)")] = None,
    all_versions: Annotated[bool, typer.Option("--all-versions", help="Also each filter version on its own")] = False,
    config: ConfigOpt = None,
    out: OutOpt = None,
):
    """How well each filter judges, against your labels: precision, noise, recall, review load, training-data check."""
    from auto_app.select.evaluate import evaluate, format_eval

    cfg = _config_or_env(config)
    store = _store(out, cfg)
    _require_db(store, "`filter eval`")
    evals = evaluate(store, filter_, cfg.select.noise_limit)
    if all_versions:
        by_filter: dict[str, list[str]] = {}
        for r in store.judgment_rows():
            if (not filter_ or r["filter"] in filter_) and r["filter_version"] not in by_filter.setdefault(r["filter"], []):
                by_filter[r["filter"]].append(r["filter_version"])
        for name, versions in sorted(by_filter.items()):
            for v in versions:
                evals += evaluate(store, [name], cfg.select.noise_limit, version=v)
    typer.echo(format_eval(evals))
    typer.echo(f"\nranges: 95% Wilson · training data: one-sided 95% exact bound vs AUTO_APP_SELECT__NOISE_LIMIT "
               f"({cfg.select.noise_limit:.0%}) · label more: auto-app filter audit")


# ---- Step 3: apply ------------------------------------------------------------------------------------

def _applications(cfg: AppConfig, store, opener=None):
    from auto_app.apply.appliers import ApplyContext, registry
    from auto_app.apply.matcher import claude_matcher
    from auto_app.apply.policy import load_policy
    from auto_app.apply.profile import load_me
    from auto_app.apply.queue import Applications

    paths = get_paths()
    policy, problem = load_policy(paths.apply_policy)
    if problem:
        typer.echo(f"✗ {problem} — fix it (`auto-app apply policy`)", err=True)
        raise typer.Exit(2)
    me = load_me(paths.home)
    ctx = None if me.problems else ApplyContext(
        me=me, matcher=claude_matcher(cfg.apply.claude_cli, cfg.apply.model), threshold=cfg.apply.match_threshold,
        headless=cfg.apply.headless, browser_profile=paths.profile("apply"), answers_file=paths.me / "answers.yaml",
        browser_profiles=paths.profiles, simplify_consent=cfg.apply.simplify_consent, agent_model=cfg.apply.agent_model,
        agent_headless=cfg.apply.agent_headless,
        agent_time_limit_start_s=cfg.apply.agent_time_limit_start_min * 60,
        agent_time_limit_submit_s=cfg.apply.agent_time_limit_submit_min * 60, logs=paths.logs)
    if ctx is not None:
        from auto_app.apply import accounts

        ctx.record_account = lambda **kw: accounts.record(store, **kw)
        ctx.find_account = lambda site: accounts.find(store, site)
    return Applications(store, policy, registry(opener), ttl=cfg.jobs.ttl_days, ctx=ctx)


@apply_app.command("profile")
def apply_profile():
    """Check profile/profile.yaml, answers.yaml and your CV; list the common answers still missing."""
    from auto_app.apply.profile import format_me, load_me

    me = load_me(get_paths().home)
    typer.echo(format_me(me))
    if me.profile is not None:  # sites that need an account: sign-up needs an email and a password
        from auto_app.apply.accounts import EMAIL, account_email, has_password

        from auto_app.apply.accounts import password_problems

        email = account_email(me)
        ok_email = bool(email and EMAIL.match(email))
        acc = me.profile.accounts
        if acc.password:
            weak = password_problems(acc.password)
            pw = ("in profile.yaml ✓" if not weak else
                  "in profile.yaml ✗ — sign-up forms would refuse it: it needs " + ", ".join(weak))
        elif ok_email and has_password(email, None):
            pw = "in the Keychain ✓"
        else:
            pw = "not set ✗ — `accounts.password` in profile/profile.yaml (sites that need an account will wait)"
        per_site = f" · own passwords for {len(acc.passwords)} site(s)" if acc.passwords else ""
        typer.echo(f"site accounts: email {email or '—'} {'✓' if ok_email else '✗'} · password {pw}{per_site}")
    if me.problems:
        raise typer.Exit(1)


@apply_app.command("policy")
def apply_policy():
    """Check config/apply.yaml (or the defaults) and print the effective policy: pace, order, applier chains."""
    from auto_app.apply.appliers import PLANNED, registry
    from auto_app.apply.policy import check_policy, format_policy, load_policy

    path = get_paths().apply_policy
    policy, problem = load_policy(path)
    typer.echo(f"{path}" + ("" if path.is_file() else " (not found — the defaults; template: config/apply.example.yaml)"))
    if problem:
        typer.echo(f"✗ {problem}", err=True)
        raise typer.Exit(1)
    typer.echo(format_policy(policy, set(registry()), set(PLANNED)))
    problems = check_policy(policy, set(registry()), set(PLANNED))
    for x in problems:
        typer.echo(f"  ✗ {x}")
    if problems:
        raise typer.Exit(1)


@apply_app.command("login")
def apply_login(
    service: Annotated[str, typer.Argument(help="simplify")],
):
    """Log in once, in the browser the automated appliers use (simplify: Simplify, with its extension loaded)."""
    if service != "simplify":
        raise typer.BadParameter("only `simplify` for now", param_hint="SERVICE")
    from playwright.sync_api import sync_playwright

    from auto_app.apply.simplify_browser import LOGIN_URL, launch, profile_in_use

    pid = profile_in_use(get_paths().profiles / "simplify")
    if pid:
        typer.echo(f"A browser on the Simplify profile is still open (pid {pid}) — close that Chrome window first (or "
                   f"run: kill {pid}), then run this again.", err=True)
        raise typer.Exit(1)
    for stale in ("SingletonLock", "SingletonCookie", "SingletonSocket"):  # left by a browser that was killed
        (get_paths().profiles / "simplify" / stale).unlink(missing_ok=True)
    typer.echo("A Chrome window opens on Simplify's login page, with the extension. Log in (if “Continue with "
               "Google” is refused, use your email), click the extension's icon to check you're logged in, then close "
               "the window.")
    try:
        with sync_playwright() as pw:
            ctx = launch(pw, get_paths().profiles, quiet=True)
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto(LOGIN_URL)
            page.bring_to_front()
            # wait until you close the window (the browser goes when its last page does); poll — robust to the
            # extension opening and closing its own tabs
            while ctx.pages:
                try:
                    ctx.pages[0].wait_for_timeout(1000)
                except Exception:  # the window was closed mid-wait
                    break
    except RuntimeError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(2)
    except Exception as e:
        log = get_paths().logs / "simplify-login.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(f"{type(e).__name__}: {e}\n")
        lines = str(e).splitlines()
        in_use = any("in use" in x.lower() or "singleton" in x.lower() for x in lines)
        typer.echo(f"The browser couldn't start: {type(e).__name__}" + (
            " — the Simplify profile is in use by another browser; quit it and try again" if in_use else ""), err=True)
        typer.echo("last lines of the browser log (all of it in " + str(log) + "):\n  " + "\n  ".join(lines[-15:]),
                   err=True)
        raise typer.Exit(1)
    typer.echo(f"saved in {get_paths().profiles / 'simplify'}")


account_app = typer.Typer(help="Site accounts for the browser agent: the email and password it signs in / up with.",
                          no_args_is_help=True)
apply_app.add_typer(account_app, name="account")


@account_app.command("set")
def account_set(
    site: Annotated[str | None, typer.Option(help="Only for this site (its host, e.g. nvidia.wd5.myworkdayjobs.com); "
                                                  "default: every site")] = None,
):
    """Save the password the agent signs in / creates accounts with — in the macOS Keychain, never in a file. The email
    is `accounts.email` in profile.yaml (default: your email)."""
    from auto_app.apply.accounts import account_email, set_password
    from auto_app.apply.profile import load_me

    from auto_app.apply.accounts import EMAIL, password_problems

    email = account_email(load_me(get_paths().home))
    if not email or not EMAIL.match(email):
        typer.echo(f"no valid email ({email!r}): set `accounts.email` (or `email`) in profile/profile.yaml first", err=True)
        raise typer.Exit(2)
    typer.echo(f"account email: {email}" + (f" · site: {site}" if site else " · every site (unless one has its own)"))
    typer.echo("sites that need an account: the agent signs in where you have one (on record, or a password set with "
               "--site), and otherwise signs up with this email and password")
    while True:
        password = typer.prompt("password", hide_input=True, confirmation_prompt=True)
        problems = password_problems(password)
        if not problems:
            break
        typer.echo("sign-up forms would refuse it — it needs " + ", ".join(problems) + "; try again", err=True)
    try:
        set_password(email, password, site)
    except RuntimeError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1)
    typer.echo("saved in the macOS Keychain (service auto-app) — the agent types it with fill_secret; Claude never sees it")


@apply_app.command("accounts")
def apply_accounts(config: ConfigOpt = None, out: OutOpt = None):
    """The site accounts the agent created or signed in to, and whether a password is set."""
    from auto_app.apply.accounts import account_email, get_password, rows
    from auto_app.apply.profile import load_me

    cfg = _config_or_env(config)
    store = _store(out, cfg)
    _require_db(store, "`apply accounts`")
    email = account_email(load_me(get_paths().home))
    typer.echo(f"account email: {email or '— (set email in profile/profile.yaml)'} · default password: "
               + ("set" if email and get_password(email) else "not set — `auto-app apply account set`"))
    found = rows(store)
    if not found:
        typer.echo("no accounts yet")
        return
    for r in found:
        how = "created by the agent" if r["created_by_agent"] else "signed in"
        typer.echo(f"  {r['site']:<44} {r['email']:<28} {how:<20} last used {str(r['last_used_at'])[:10]}"
                   + (f" · {r['note']}" if r["note"] else ""))


@apply_app.command("queue")
def apply_queue(config: ConfigOpt = None, out: OutOpt = None):
    """The jobs you queued (want to apply), in the policy's order, with each one's platform, applier chain and status."""
    cfg = _config_or_env(config)
    store = _store(out, cfg)
    _require_db(store, "`apply queue`")
    rows = _applications(cfg, store).queue()
    if not rows:
        typer.echo("nothing queued — mark jobs `a` (want to apply) in `auto-app job browse`")
        return
    for j in rows:
        app_ = j["application"]
        status = f"{app_['status']} ({app_.get('applier') or '—'})" if app_ else "not started"
        typer.echo(f"#{j['job_id']:<5} {(j.get('company') or '')[:18]:<18} {(j.get('title') or '')[:44]:<44} "
                   f"{j['platform']:<20} {' → '.join(j['chain']):<32} {status}")
    typer.echo(f"\n{len(rows)} queued · `auto-app apply run` starts them · `auto-app apply inbox` for what needs you")


@apply_app.command("run")
def apply_run(
    job: Annotated[Optional[list[int]], typer.Option("--job", "-j", help="Only these jobs (repeatable)")] = None,
    config: ConfigOpt = None,
    out: OutOpt = None,
):
    """Start applications for the queued jobs (automated applying only — off by default: AUTO_APP_APPLY__AUTOMATED)."""
    from auto_app.apply.profile import load_me

    cfg = _config_or_env(config)
    if not cfg.apply.automated:
        typer.echo("automated applying is off — apply by hand from the Apply section (`auto-app ui`, F3 → Queued: "
                   "enter opens the link and your CV, a marks it applied). To turn it back on: "
                   "AUTO_APP_APPLY__AUTOMATED=true")
        return
    store = _store(out, cfg)
    _require_db(store, "`apply run`")
    me = load_me(get_paths().home)
    if me.problems:
        typer.echo("fix your profile first (`auto-app apply profile`):\n  " + "\n  ".join(me.problems), err=True)
        raise typer.Exit(2)
    apps = _applications(cfg, store)
    counts = apps.run(job)
    if not counts:
        typer.echo("nothing to start — every queued job already has an application (`auto-app apply queue`)")
        return
    typer.echo("started: " + ", ".join(f"{n} {k}" for k, n in sorted(counts.items())))
    if counts.get("handoff"):
        typer.echo("hand-offs are waiting for you: auto-app apply inbox")


@apply_app.command("inbox")
def apply_inbox(config: ConfigOpt = None, out: OutOpt = None):
    """Open the inbox: hand-offs to apply to (Simplify / manual), failed applications, done."""

    cfg = _config_or_env(config)
    store = _store(out, cfg)
    _require_db(store, "`apply inbox`")
    _check_themes(cfg)
    if not sys.stdout.isatty():
        typer.echo("`apply inbox` is a full-screen app — run it in a terminal (or use `apply list`)", err=True)
        raise typer.Exit(2)
    from auto_app.select.filters import FILTERS_DIR, load_filters

    _run_ui(cfg, store, load_filters(FILTERS_DIR), "inbox")


@apply_app.command("list")
def apply_list(
    status: Annotated[Optional[str], typer.Option(help="Only this status: queued | handoff | failed | submitted | dropped")] = None,
    config: ConfigOpt = None,
    out: OutOpt = None,
):
    """Application history: every application, its status, applier and attempts."""
    cfg = _config_or_env(config)
    store = _store(out, cfg)
    _require_db(store, "`apply list`")
    apps = _applications(cfg, store)
    rows = apps.rows((status,) if status else None)
    if not rows:
        typer.echo("no applications" + (f" with status {status}" if status else "") + " yet")
        return
    for r in rows:
        when = f" {str(r['submitted_at'])[:10]}" if r.get("submitted_at") else ""
        tried = len(apps.attempts(r["application_id"]))
        typer.echo(f"app {r['application_id']:<4} job #{r['job_id']:<5} {(r.get('company') or '')[:18]:<18} "
                   f"{(r.get('title') or '')[:40]:<40} {r['platform']:<20} {r['status']}{when} "
                   f"({r.get('applier') or '—'}, {tried} attempt{'s' if tried != 1 else ''})")


# ---- serve: the app running by itself (docs/step4-run.md) ---------------------------------------------------

serve_app = typer.Typer(help="Run by itself: collect in rounds (until caught up), filter each round's jobs as a batch, "
                             "pause, repeat — docs/step4-run.md.", no_args_is_help=True)
app.add_typer(serve_app, name="serve")


def _serve_paths():
    p = get_paths()
    return p.home / "config" / "serve.yaml", p.home / "data" / "serve.json"


def _serve_config():
    from auto_app.serve import load_serve_config

    path, _ = _serve_paths()
    try:
        return load_serve_config(path)
    except Exception as e:
        typer.echo(f"{path}: {e}", err=True)
        raise typer.Exit(2)


@serve_app.command("run")
def serve_run(
    once: Annotated[bool, typer.Option("--once", help="One round now (if it may start), then exit")] = False,
    config: ConfigOpt = None,
    out: OutOpt = None,
):
    """The loop, in the foreground (launchd runs this — `serve install`). Ctrl-C stops it; what's saved stays."""
    import fcntl

    from auto_app.collect.plan import Selection
    from auto_app.core.notify import notify
    from auto_app.pipeline import RunLocked, run_lock, run_pipeline
    from auto_app.select.engine import SelectOptions, run_select
    from auto_app.select.filters import FILTERS_DIR, load_filters
    from auto_app.select.setup import SetupError, make_runner
    from auto_app.serve import BrokenFilters, NotNow, Serve, State, format_round, keep_awake

    cfg = _config(config)
    store = _store(out, cfg)
    _require_db(store, "`serve`")
    scfg = _serve_config()
    _, state_path = _serve_paths()
    paths = get_paths()
    lock_file = open(paths.home / "data" / "serve.lock", "a+")
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        typer.echo("`serve` is already running (data/serve.lock) — `auto-app serve status`", err=True)
        raise typer.Exit(3)
    all_sources = [t.source for t in _tasks(cfg, Selection())]

    def collect(sources, goals):
        opts = CollectOptions(headless=scfg.headless, source_goals=goals)
        sel = Selection(sources=sources) if sources is not None else Selection()
        if sources == []:
            return {}, 0
        try:
            with run_lock(paths.home / "data" / "run.lock"):
                res = asyncio.run(run_pipeline(cfg, paths, store, opts, sel, default_registry(), collect=True))
        except RunLocked as e:
            raise RuntimeError(f"{e} — a manual `auto-app run` is collecting; this round skips")
        return res.batch or {}, res.new_jobs

    def judge():
        fs = load_filters(FILTERS_DIR)
        if fs.broken:
            raise BrokenFilters({n: f"doesn't load: {why}" for n, why in fs.broken.items()})
        chosen = list(fs.filters.values())
        if not chosen:
            raise BrokenFilters({"(none)": "no filters in filters/ — nothing can be judged"})
        try:
            runner = make_runner(cfg, chosen)
        except SetupError as e:
            raise BrokenFilters({"(runner)": str(e)})
        return run_select(store, cfg, chosen, runner,
                          SelectOptions(parallel=scfg.filter.workers or cfg.select.parallel))

    import signal
    import threading

    def force_exit():  # the clean stop didn't finish in time (a browser driver's tasks hang on): leave anyway
        State(state_path).update(status="not running", pid=None)
        if awake:
            awake.terminate()
        logging.shutdown()
        os._exit(0)

    def on_stop(signum, frame):  # Ctrl-C, or launchd stopping the service (SIGTERM)
        threading.Timer(15, force_exit).start()  # everything collected is saved already: nothing is lost
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, on_stop)
    signal.signal(signal.SIGINT, on_stop)
    awake = None
    awake = keep_awake()
    serve = Serve(scfg, State(state_path), collect, judge, notify, store=store,
                  rounds_log=paths.logs / "serve-rounds.jsonl")
    typer.echo(f"serve: rounds until caught up (cap {scfg.round.time_limit or 'none'}), then a {scfg.interval} pause · "
               f"window {scfg.window.days} {scfg.window.start}–{scfg.window.end} · state {state_path}")
    try:
        serve.run_forever(all_sources, once=once)
        if once:
            typer.echo(format_round(State(state_path).read().get("last_round") or {}))
    except NotNow as e:
        typer.echo(f"no round now: {e}")
    except KeyboardInterrupt:
        typer.echo("\nstopped — everything saved so far is kept")
    finally:
        State(state_path).update(status="not running", pid=None)
        if awake:
            awake.terminate()
        logging.shutdown()
        # leave now: a stop mid-collect can leave the browser driver's tasks behind, which would keep the process alive
        os._exit(0)


@serve_app.command("status")
def serve_status():
    """What `serve` is doing: running / paused / stopped, the next round, the last round's report."""
    from auto_app.serve import State, describe

    _, state_path = _serve_paths()
    typer.echo(describe(_serve_config(), State(state_path).read()))


@serve_app.command("pause")
def serve_pause(
    until: Annotated[Optional[str], typer.Option("--until", help="Until when: a time (2026-10-07 09:00, 09:00) or a "
                                                                 "duration (2h, 30m); default: until you resume")] = None,
):
    """No new rounds (judging what's queued goes on)."""
    from auto_app.core.duration import parse_duration
    from auto_app.serve import State

    _, state_path = _serve_paths()
    st = State(state_path)
    if until is None:
        st.update(paused=True, paused_until=None)
        typer.echo("paused until you resume (`auto-app serve resume`)")
        return
    now = datetime.now()
    try:
        when = now + timedelta(seconds=parse_duration(until))
    except ValueError:
        try:
            when = datetime.fromisoformat(until) if len(until) > 5 else datetime.combine(
                now.date(), datetime.strptime(until, "%H:%M").time())
            if when <= now and len(until) <= 5:
                when += timedelta(days=1)
        except ValueError:
            raise typer.BadParameter(f"not a time or duration: {until!r}", param_hint="--until")
    st.update(paused=None, paused_until=when.isoformat(timespec="minutes"))
    typer.echo(f"paused until {when:%Y-%m-%d %H:%M}")


@serve_app.command("resume")
def serve_resume():
    """Rounds again: after a pause, or after you fixed a broken filter (then the backlog is judged first)."""
    from auto_app.serve import State

    _, state_path = _serve_paths()
    st = State(state_path)
    was = st.read()
    st.update(paused=None, paused_until=None, stopped=None, usage_until=None,
              filter_pending=True if was.get("stopped") else None)
    typer.echo("resumed" + (" — the backlog is judged first, then the next round" if was.get("stopped") else ""))


@serve_app.command("install")
def serve_install(dry_run: Annotated[bool, typer.Option("--dry-run", help="Show the launchd plist; install nothing")] = False):
    """Keep `serve run` running in the background (macOS launchd, KeepAlive): it starts at log-in and after a crash."""
    import plistlib

    from auto_app import serve as sv

    plist = sv.build_plist()
    if dry_run:
        typer.echo(plistlib.dumps(plist).decode())
        return
    path = sv.install(plist)
    typer.echo(f"installed {path} — `auto-app serve status`; log: data/logs/serve.log")


@serve_app.command("remove")
def serve_remove():
    """Stop and remove the background `serve`."""
    from auto_app import serve as sv

    typer.echo("removed" if sv.remove() else "not installed")


# Subcommands are listed in workflow order, not in the order they're defined above.
_ORDER = {
    source_app: ["list", "show", "add", "login", "health", "plan"],
    collect_app: ["run", "probe", "stats"],
    process_app: ["parse", "normalize", "dedup"],
    job_app: ["list", "show", "browse", "mark"],
    filter_app: ["list", "show", "run", "why", "audit", "eval", "reparse"],
    data_app: ["inspect", "export", "import", "purge"],
    schedule_app: ["install", "show", "remove"],
    apply_app: ["profile", "policy", "login", "accounts", "queue", "run", "inbox", "list"],
    serve_app: ["run", "status", "pause", "resume", "install", "remove"],
}
for _group, _names in _ORDER.items():
    _group.registered_commands.sort(key=lambda c: _names.index(c.name))

if __name__ == "__main__":
    app()
