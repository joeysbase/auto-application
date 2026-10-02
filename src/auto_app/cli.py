import asyncio
import json
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
from auto_app.core.config import GENERIC_FIELDS, AppConfig, load_config
from auto_app.core.logging import setup_logging
from auto_app.core.models import utcnow
from auto_app.settings import PROJECT_ROOT, get_paths
from auto_app.sources import SOURCES, get_adapter
from auto_app.sources.filters import describe
from auto_app.sources.registry import default_registry

app = typer.Typer(no_args_is_help=True, add_completion=False, help="Collect job postings (raw collection stage).")

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
    _debug_files = debug_files


def _store(db: Path | None = None, cfg: AppConfig | None = None):
    """The database store — or, with --debug-files (or `debug_files: true` in the config), the file store
    under data/raw/ *instead* (nothing is written to the database; `import-files` loads it later)."""
    from auto_app.core.db.store import DbStore

    paths = get_paths()
    if cfg is None and paths.config_file.exists():
        try:
            cfg = load_config(paths.config_file)
        except Exception:  # config problems are reported by the commands that need the config
            cfg = None
    if _debug_files or (cfg is not None and cfg.debug_files):
        return FileStore(paths.raw)
    return DbStore(db or paths.db, paths)


def _require_db(store, what: str) -> None:
    if not hasattr(store, "create_job"):
        typer.echo(f"{what} needs the database; this command is running with debug files only "
                   f"({store.describe()}). Run it without --debug-files (and `debug_files: false`), "
                   f"or load the files first with `auto-app import-files`.", err=True)
        raise typer.Exit(2)


def _config(path: Path | None) -> AppConfig:
    path = path or get_paths().config_file
    if not path.exists():
        example = PROJECT_ROOT / "config" / "searches.example.yaml"
        typer.echo(f"config not found: {path}\ncopy the example:  cp {example} {path}", err=True)
        raise typer.Exit(2)
    return load_config(path)


def _check_source(source: str) -> None:
    """`login` works for browser sources that need a signed-in profile."""
    reg = default_registry()
    usable = sorted(n for n, i in reg.plugins.items() if i.kind == "browser" and getattr(i.cls, "needs_login", True))
    if source not in usable:
        raise typer.BadParameter(f"expected one of {', '.join(usable)}")


@app.command()
def login(
    source: str,
    verify: Annotated[bool, typer.Option(help="Check the saved session afterwards (headless)")] = True,
    config: ConfigOpt = None,
):
    """Open a normal (non-automated) Chrome on SOURCE's profile so you can log in by hand."""
    _check_source(source)
    cfg = _config(config) if (config or get_paths().config_file.exists()) else AppConfig()
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
        typer.echo("still logged out — run `auto-app login` again and make sure the site shows you as signed in", err=True)
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


SiteOpt = Annotated[Optional[list[str]], typer.Option("--source", "-s", "--site", help="Source(s) to run, by plugin name (repeatable)")]
CompanyOpt = Annotated[Optional[list[str]], typer.Option("--company", "-c", help="Same as --source (kept for old scripts)")]
JobSitesOnly = Annotated[bool, typer.Option("--job-sites", help="Only job sites (sources with many employers)")]
CompaniesOnly = Annotated[bool, typer.Option("--companies", help="Only company sources (one employer each)")]
SearchOpt = Annotated[Optional[list[str]], typer.Option("--search", help="Search name(s) from config; default: all")]


@app.command()
def collect(
    site: SiteOpt = None,
    company: CompanyOpt = None,
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
    sel = _selection(site, company, job_sites_only, companies_only, search)
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
    typer.echo(f"\ninspect with:  auto-app inspect{' --db ' + str(out) if out else ''}")


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
    company: CompanyOpt = None,
    job_sites_only: JobSitesOnly = False,
    companies_only: CompaniesOnly = False,
    search: SearchOpt = None,
    collect_: Annotated[bool, typer.Option("--collect/--no-collect", help="Collect first (off: only process saved postings)")] = True,
    do_purge: Annotated[bool, typer.Option("--purge", help="Also apply retention (auto-app purge) at the end")] = False,
    max_pages: Annotated[Optional[int], typer.Option(help="Cap result pages per job-site search")] = None,
    headless: bool = False,
    force: Annotated[bool, typer.Option(help="Ignore source cooldowns")] = False,
    manual_checks: Annotated[bool, typer.Option(help="Wait for you to complete security checks in the window")] = False,
    scheduled: Annotated[bool, typer.Option(help="Enforce run window and minimum interval; notify when done (for launchd)")] = False,
    config: ConfigOpt = None,
    out: OutOpt = None,
):
    """The whole of Step 1: collect → parse → normalize → dedup (→ purge)."""
    from auto_app.core.notify import notify
    from auto_app.pipeline import RunLocked, format_pipeline, run_lock, run_pipeline

    cfg = _config(config)
    store = _store(out, cfg)
    sel = _selection(site, company, job_sites_only, companies_only, search)
    tasks = _tasks(cfg, sel)
    do_collect = collect_
    if scheduled and do_collect and not _apply_schedule(cfg, store, sel, tasks):
        do_collect = False  # nothing may collect right now; still process what's saved
    opts = CollectOptions(headless=headless, max_pages=max_pages, force=force, manual_checks=manual_checks)
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


@app.command("purge")
def purge_cmd(
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Show what would be removed; remove nothing")] = False,
    vacuum: Annotated[bool, typer.Option(help="Compact the database file afterwards")] = False,
    config: ConfigOpt = None,
    out: OutOpt = None,
):
    """Expire jobs (jobs.ttl_days: delete, or archive recommended ones), then drop old raw data, sightings and logs."""
    from auto_app.core.db.purge import purge

    cfg = _config(config) if (config or get_paths().config_file.exists()) else AppConfig()
    store = _store(out, cfg)
    _require_db(store, "`purge`")
    typer.echo(purge(store, cfg.retention, ttl=cfg.jobs.ttl_days, dry_run=dry_run, vacuum=vacuum).format())


schedule_app = typer.Typer(no_args_is_help=True, help="Run `auto-app run` automatically (macOS launchd).")
app.add_typer(schedule_app, name="schedule")


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


@app.command()
def parse(
    site: SiteOpt = None,
    company: CompanyOpt = None,
    reparse: Annotated[bool, typer.Option(help="Parse again even if already parsed with the current parser")] = False,
    config: ConfigOpt = None,
    out: OutOpt = None,
):
    """Turn saved raw postings into common fields (parsed.json) and show field coverage per source."""
    from auto_app.process.parse import format_coverage, parse_postings

    cfg = _config(config) if (config or get_paths().config_file.exists()) else AppConfig()
    sources = [*(site or []), *(company or [])] or None
    stats = parse_postings(_store(out, cfg), cfg, default_registry(), sources, reparse=reparse)
    typer.echo(format_coverage(stats) if stats else "no saved postings")


@app.command()
def normalize(
    site: SiteOpt = None,
    company: CompanyOpt = None,
    again: Annotated[bool, typer.Option("--again", help="Normalize again even if up to date")] = False,
    config: ConfigOpt = None,
    out: OutOpt = None,
):
    """Compute each posting's dedup keys (normalized.json): company, title, employer job key, description hash."""
    from auto_app.process.normalize import format_normalize, normalize_postings

    cfg = _config(config) if (config or get_paths().config_file.exists()) else AppConfig()
    store = _store(out, cfg)
    sources = [*(site or []), *(company or [])] or None
    stats = normalize_postings(store, default_registry(), sources, again=again)
    typer.echo(format_normalize(stats) if stats else "no saved postings")


@app.command()
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

    cfg = _config(config) if (config or get_paths().config_file.exists()) else AppConfig()
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
        typer.echo(f"jobs now: {len(store.jobs())} · list them with `auto-app jobs`")


STATES = ("not_evaluated", "un_recommended", "recommended", "applied", "not_applied")


@app.command()
def jobs(
    job_id: Annotated[Optional[int], typer.Argument(help="Show one job: its details, postings and matching keys")] = None,
    source: Annotated[Optional[list[str]], typer.Option("--source", "-s", help="Only jobs found on this source (repeatable)")] = None,
    company: Annotated[Optional[str], typer.Option("--company", "-c", help="Only jobs whose company contains this text")] = None,
    state: Annotated[Optional[str], typer.Option(
        help="Only jobs in this state: not_evaluated | un_recommended | recommended (undecided) | applied | not_applied")] = None,
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
        typer.echo("no archived jobs" if archived else "no jobs yet — run `auto-app dedup`")
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


@app.command()
def mark(
    job_ids: Annotated[list[int], typer.Argument(help="Job number(s)", metavar="JOB_ID...")],
    to: Annotated[str, typer.Option("--as", help="recommended | un_recommended | applied | not_applied | not_evaluated")],
    config: ConfigOpt = None,
    out: OutOpt = None,
):
    """Set jobs' state by hand (until Steps 2–3 do it): Step 2's verdict and/or your decision."""
    cfg = _config(config) if (config or get_paths().config_file.exists()) else AppConfig()
    store = _store(out, cfg)
    _require_db(store, "`mark`")
    if to not in STATES:
        raise typer.BadParameter(f"expected one of {', '.join(STATES)}", param_hint="--as")
    step2, decision = {
        "not_evaluated": (None, None),
        "un_recommended": ("un_recommended", None),
        "recommended": ("recommended", None),
        "applied": ("recommended", "applied"),  # you only decide on recommended jobs
        "not_applied": ("recommended", "not_applied"),
    }[to]
    for job_id in job_ids:
        j = store.job(job_id)
        if not j:
            typer.echo(f"no job #{job_id}", err=True)
            raise typer.Exit(1)
        if j.get("archived_at"):
            typer.echo(f"job #{job_id} is archived (a record); its state can't change", err=True)
            raise typer.Exit(1)
        keep_step2 = to in ("applied", "not_applied") and j.get("step2") == "recommended"
        store.set_job_state(job_id, step2=... if keep_step2 else step2, decision=decision)
    store.refresh_job_dates(cfg.jobs.ttl_days)
    for job_id in job_ids:
        j = store.job(job_id)
        typer.echo(f"job #{job_id}: {j['state']}  (expires {(j.get('expires_at') or '—')[:10]})  {j.get('title') or ''}")


@app.command()
def plan(
    site: SiteOpt = None,
    company: CompanyOpt = None,
    job_sites_only: JobSitesOnly = False,
    companies_only: CompaniesOnly = False,
    search: SearchOpt = None,
    config: ConfigOpt = None,
):
    """Show what `collect` would run, and where each search field is applied (native / local / deferred)."""
    cfg = _config(config)
    typer.echo(format_plan(_tasks(cfg, _selection(site, company, job_sites_only, companies_only, search))))


@app.command()
def plugins(name: Annotated[Optional[str], typer.Argument(help="Show one plugin (or platform) in detail")] = None):
    """List sources (one plugin each: built-in job sites and plugins/), or show one's search options."""
    from auto_app.platforms import PLATFORMS

    reg = default_registry()
    if not name:
        for info in reg.plugins.values():
            what = f"company {info.company} ({info.platform})" if info.is_company else f"job site ({info.kind})"
            typer.echo(f"{info.name:<14} {what:<36} {_rel(info.origin)}")
        typer.echo(f"\nplatforms (bases for company plugins): {', '.join(PLATFORMS)}  — add one with `auto-app add-company URL`")
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
        typer.echo(f"{name} — platform (a base for company plugins; `auto-app add-company URL` creates one)")
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


@app.command("add-company")
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
                   f"(options: `auto-app plugins {name}`), then check it with `auto-app plan -s {name}`")


def company_plugin_source(name: str, company: str, platform: str, board: str) -> str:
    from auto_app.platforms import PLATFORMS

    base = PLATFORMS[platform]
    cls = "".join(w[:1].upper() + w[1:] for w in re.split(r"[^A-Za-z0-9]+", company)) or "Company"
    if cls[0].isdigit():
        cls = "C" + cls
    q = json.dumps  # double-quoted, escaped Python string literals
    return (
        f'"""{company} careers ({platform}). Written by `auto-app add-company`; edit freely —\n'
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
             f"    #     site_filters: {{}}   # this board's own options: `auto-app plugins {name}`\n")
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


@app.command()
def probe(
    source: str,
    job: Annotated[str, typer.Argument(help="Job id or job URL")],
    screenshots: bool = True,
    capture_all_json: bool = False,
    config: ConfigOpt = None,
    out: OutOpt = None,
):
    """Fetch a single job page (browser sources) and show what was captured."""
    cfg = _config(config) if (config or get_paths().config_file.exists()) else AppConfig()
    store = _store(out)
    try:
        info = default_registry().get(source)
    except KeyError as e:
        raise typer.BadParameter(e.args[0])
    if info.kind != "browser":
        raise typer.BadParameter(f"{source} is collected through an API; use `collect -s {source}` instead")
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


@app.command()
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


@app.command("import-files")
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


@app.command()
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


@app.command()
def stats(batch: Annotated[Optional[str], typer.Argument(help="Batch id; default: latest")] = None, out: OutOpt = None):
    """Show search runs and outcomes for a batch."""
    typer.echo(format_batch(_store(out), batch))


@app.command()
def sources(
    reset: Annotated[Optional[str], typer.Option(help="Clear cooldown / login status for a source")] = None,
    out: OutOpt = None,
):
    """Show per-source health (ok / needs_login / cooldown)."""
    store = _store(out)
    if reset:
        store.update_health(reset, status="ok", cooldown_until=None)
    health = store.health()
    cfg = _config(None) if get_paths().config_file.exists() else AppConfig()
    names = list(dict.fromkeys([*(n for n, sc in cfg.sources.items() if sc.searches), *health]))
    for s in names or SOURCES:
        h = health.get(s, {"status": "ok"})
        extra = f" until {h['cooldown_until']}" if h.get("cooldown_until") else ""
        reason = f" — {h['last_block_reason']}" if h.get("last_block_reason") and h["status"] != "ok" else ""
        typer.echo(f"{s:<10} {h['status']}{extra}{reason}  (last ok: {h.get('last_ok_at', '-')})")


if __name__ == "__main__":
    app()
