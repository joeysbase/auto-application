"""Turn the config into collection tasks: which source runs which resolved searches, and where
each search field is applied (native / local / deferred). docs/step1-collect.md §2.4–2.5.

Every source is a plugin; `sources.<plugin name>.searches` says what to search there. A search
resolves as: defaults ← the search it copies (`from: <source>/<search>`, recursively) ← its own fields.
"""

from dataclasses import dataclass, field

from pydantic import ValidationError

from auto_app.core.config import GENERIC_FIELDS, AppConfig, SearchFields, SourceLimits, SourceSearch
from auto_app.core.search import ResolvedSearch
from auto_app.sources.filters import level_of, unmapped_values
from auto_app.sources.registry import Registry


class ConfigError(ValueError):
    pass


@dataclass
class SourceTask:
    source: str  # the plugin name: config key, storage name
    kind: str  # "job_site" | "company"
    plugin: object  # SourceAdapter (browser) or ApiSource (api)
    platform: str  # the platform base (workday …), or the source itself
    limits: SourceLimits
    board: str | None = None
    company: str | None = None  # company sources: the source name
    display_name: str | None = None  # company sources: the employer's name
    searches: list[ResolvedSearch] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def is_api(self) -> bool:
        return getattr(self.plugin, "kind", "") == "api"


@dataclass
class Selection:
    sources: list[str] | None = None  # None = every source with searches in the config
    search_names: list[str] | None = None
    job_sites: bool = True  # include job sites (many employers)
    companies: bool = True  # include company sources (one employer)


def _days(value) -> int | None:
    """Config value → resolved: a number of days, or None for "all" (no date limit)."""
    return None if value == "all" else value


def _levels(plugin, search: ResolvedSearch) -> dict[str, str]:
    return {f: level_of(plugin.filters, f) for f in GENERIC_FIELDS if search.active(f)}


def _explicit_fields(own: SearchFields) -> set[str]:
    return {f for f in GENERIC_FIELDS if getattr(own, f) not in (None, [])}


def _find(cfg: AppConfig, ref: str) -> SourceSearch:
    source, sep, name = ref.partition("/")
    if not sep or not source or not name:
        raise ConfigError(f"`from: {ref}` should be <source>/<search>, e.g. linkedin/ml-engineer-us")
    sc = cfg.sources.get(source)
    if sc is None:
        raise ConfigError(f"`from: {ref}`: no source {source!r} in the config")
    for s in sc.searches:
        if s.search_name == name:
            return s
    names = [s.search_name for s in sc.searches] or "none"
    raise ConfigError(f"`from: {ref}`: {source} has no search {name!r} (its searches: {names})")


def _chain(cfg: AppConfig, search: SourceSearch, where: str) -> list[SearchFields]:
    """The search, then each search it copies from (nearest first)."""
    chain, seen, cur = [search], [where], search
    while cur.from_:
        if cur.from_ in seen:
            raise ConfigError(f"{where}: `from` loops: {' → '.join([*seen, cur.from_])}")
        seen.append(cur.from_)
        cur = _find(cfg, cur.from_)
        chain.append(cur)
    return chain


def _resolve(cfg: AppConfig, name: str, chain: list[SearchFields]) -> dict:
    """Effective field values: defaults ← copied searches ← own."""
    d = cfg.defaults

    def pick(attr, default):
        for src in chain:
            if getattr(src, attr) is not None:
                return getattr(src, attr)
        return default

    return dict(
        name=name,
        keywords=pick("keywords", []),
        locations=pick("locations", []),
        posted_within_days=_days(pick("posted_within_days", d.posted_within_days)),
        workplace_types=pick("workplace_types", []),
        employment_types=pick("employment_types", []),
        experience_levels=pick("experience_levels", []),
        max_pages=pick("max_pages", d.max_pages_per_search),
        known_streak=pick("early_stop_known_streak", d.early_stop_known_streak),
        card_filters=pick("card_filters", d.card_filters),
    )


def _missing_plugin(cfg: AppConfig, name: str, error: str) -> str:
    old = cfg.legacy_companies.get(name)
    if old and old.get("board"):
        return (f"source {name!r}: no plugin — company sources are plugin files now. Create it with:\n"
                f"    auto-app source add {old['board']} --name {name}"
                + (f" --company {old['display_name']!r}" if old.get("display_name") else ""))
    return f"source {name!r}: {error}"


def build_tasks(cfg: AppConfig, registry: Registry, sel: Selection | None = None) -> list[SourceTask]:
    sel = sel or Selection()
    tasks: list[SourceTask] = []
    errors: list[str] = []
    matched_names: set[str] = set()

    for name in sel.sources or []:
        if name not in cfg.sources or not cfg.sources[name].searches:
            if name not in registry.plugins:
                try:
                    registry.get(name)
                except KeyError as e:
                    errors.append(_missing_plugin(cfg, name, e.args[0]))
            else:
                errors.append(f"source {name!r} has no searches; add them under `sources: {name}: searches:` in the config")

    for name, sc in cfg.sources.items():
        if sel.sources is not None and name not in sel.sources:
            continue
        try:
            info = registry.get(name)
        except KeyError as e:
            errors.append(_missing_plugin(cfg, name, e.args[0]))
            continue
        kind = "company" if info.is_company else "job_site"
        if (kind == "company" and not sel.companies) or (kind == "job_site" and not sel.job_sites):
            continue
        wanted = [s for s in sc.searches if not sel.search_names or s.search_name in sel.search_names]
        matched_names.update(s.search_name for s in wanted)
        if not wanted:
            continue
        limits = cfg.limits(name, info.cls)
        plugin = registry.create(name, limits.base_url)
        task = SourceTask(name, kind, plugin, info.platform or name, limits, board=info.board,
                          company=name if info.is_company else None, display_name=info.company)
        for s in wanted:
            where = f"{name}/{s.search_name}"
            try:
                chain = _chain(cfg, s, where)
            except ConfigError as e:
                errors.append(str(e))
                continue
            try:
                site_filters = plugin.SiteFilters(**s.site_filters)
            except ValidationError as e:
                msgs = "; ".join(f"{'.'.join(map(str, x['loc']))}: {x['msg']}" for x in e.errors())
                errors.append(f"{where} site_filters: {msgs}  (options: `auto-app source show {name}`)")
                continue
            if s.site_url and info.kind != "browser":
                errors.append(f"{where}: `site_url` is only for browser sources; {name} is searched through an API")
                continue
            rs = ResolvedSearch(**_resolve(cfg, s.search_name, chain), site_filters=site_filters, site_url=s.site_url)
            rs.levels = _levels(plugin, rs)
            explicit = _explicit_fields(s)
            for f in rs.deferred:
                if f in explicit:
                    errors.append(f"{where}: `{f}` is not supported by {name} (remove it, and state it in your Step 2 command "
                                  f"if it matters — `auto-app source show {name}` lists what it supports)")
                else:
                    task.warnings.append(f"{s.search_name}: {f} (copied from {s.from_}) not supported by {name} → not filtered (state it in your Step 2 command if it matters)")
            check = getattr(plugin, "check_search", None)
            if check:  # plugin-specific limits of its filters
                errs, notes = check(rs)
                errors.extend(f"{where}: {e}" for e in errs)
                task.warnings.extend(f"{s.search_name}: {n}" for n in notes)
            for f in rs.levels:
                bad = unmapped_values(plugin.filters.get(f), getattr(rs, f))
                if bad:
                    task.warnings.append(f"{s.search_name}: {name} has no {f} value for {bad}; not filtered (state it in your Step 2 command if it matters)")
            task.searches.append(rs)
        if task.searches:
            tasks.append(task)

    if sel.search_names:
        unknown = sorted(set(sel.search_names) - matched_names)
        if unknown:
            errors.append(f"no search named {unknown} in the selected sources")
    if errors:
        raise ConfigError("\n".join(errors))
    return tasks


def format_plan(tasks: list[SourceTask]) -> str:
    out = []
    for t in tasks:
        if t.kind == "company":
            head = f"{t.source}  (company {t.display_name} · {t.platform}" + (f", board {t.board})" if t.board else ")")
        else:
            head = f"{t.source}  (job site)"
        out.append(head)
        for s in t.searches:
            out.append(f"  search {s.name}")
            for f in GENERIC_FIELDS:
                if s.active(f):
                    val = getattr(s, f)
                    out.append(f"    {f:<20} {s.levels.get(f, '-'):<9} {val}")
                elif f == "posted_within_days":
                    out.append(f"    {f:<20} {'-':<9} all (no date limit)")
            if s.site_filters is not None:
                sf = s.site_filters.model_dump(exclude_defaults=True)
                if sf:
                    out.append(f"    {'site_filters':<20} {'plugin':<9} {sf}")
            if s.site_url:
                out.append(f"    {'site_url':<20} {'native':<9} {s.site_url}")
            cf = s.card_filters
            if cf.exclude_title_words or cf.exclude_companies:
                out.append(f"    {'card_filters':<20} {'local':<9} title≠{cf.exclude_title_words} company≠{cf.exclude_companies}")
            if not t.is_api:
                sorted_ok = getattr(t.plugin, "sorted_by_date", True)
                out.append(f"    {'early stop':<20} {'yes' if sorted_ok else 'no (not sorted by date)'} after {s.known_streak} known; max {s.max_pages} pages")
        for w in t.warnings:
            out.append(f"  ! {w}")
        out.append("")
    return "\n".join(out) or "nothing to collect"
