"""searches.yaml models.

Layout (see docs/step1-collect.md §2.4):
    defaults:   settings shared by every search
    sources:    one entry per source (= plugin name): its named `searches` and its `limits`
Which board a company source reads, and its display name, live in its plugin (plugins/companies/).
"""

import logging
from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

log = logging.getLogger(__name__)

WorkplaceType = Literal["onsite", "remote", "hybrid"]
EmploymentType = Literal["full_time", "part_time", "contract", "temporary", "internship"]
ExperienceLevel = Literal["internship", "entry", "associate", "mid_senior", "director", "executive"]
# Days back to look for postings, or "all" for no date limit.
PostedWithin = Annotated[int, Field(ge=1)] | Literal["all"]

# Generic search fields a plugin can support natively, locally, or not at all (deferred to Step 2).
GENERIC_FIELDS = (
    "keywords",
    "locations",
    "posted_within_days",
    "workplace_types",
    "employment_types",
    "experience_levels",
)


class CardFilters(BaseModel):
    model_config = ConfigDict(extra="forbid")
    exclude_companies: list[str] = []
    exclude_title_words: list[str] = []


class SearchFields(BaseModel):
    """Generic search fields, shared by every source. None = not set here (inherit)."""

    model_config = ConfigDict(extra="forbid")
    keywords: list[str] | None = None
    locations: list[str] | None = None
    posted_within_days: PostedWithin | None = None  # None = inherit; "all" = no date limit
    workplace_types: list[WorkplaceType] | None = None
    employment_types: list[EmploymentType] | None = None
    experience_levels: list[ExperienceLevel] | None = None
    max_pages: int | None = None
    early_stop_known_streak: int | None = None
    card_filters: CardFilters | None = None


class SourceSearch(SearchFields):
    """One named search of a source. `from: <source>/<search>` copies that search's generic fields
    (keywords, locations, dates …); fields set here override them. `site_url` and `site_filters`
    belong to this source and are never copied."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    name: str | None = None  # required unless `from` is given (then defaults to the copied search's name)
    from_: str | None = Field(None, alias="from")
    site_url: str | None = None  # browser sources: a page-1 search URL copied from the browser
    site_filters: dict[str, Any] = {}  # this source's own options, validated by its plugin

    @model_validator(mode="after")
    def _named(self):
        if not self.name and not self.from_:
            raise ValueError("a search needs a `name` (or `from: <source>/<search>`)")
        return self

    @property
    def search_name(self) -> str:
        return self.name or (self.from_ or "").split("/", 1)[-1]


class SourceLimits(BaseModel):
    base_url: str | None = None  # e.g. https://northeastern.joinhandshake.com
    min_delay_s: float = 2
    max_delay_s: float = 6
    long_pause_every: int = 15
    long_pause_s: tuple[float, float] = (20, 60)
    # Job pages opened, across runs: pause when the hourly limit is reached, stop at the daily ceiling.
    max_details_per_hour: int = 100
    max_details_per_day: int = 600
    cooldown_hours_on_block: float = 24
    # Hand security checks to the user (headed runs only) instead of stopping the source.
    manual_checks: bool = False
    max_manual_checks: int = 5  # per source per run; beyond this the source stops
    manual_check_timeout_s: float = 300


DEFAULT_LIMITS: dict[str, SourceLimits] = {
    "linkedin": SourceLimits(
        min_delay_s=4,
        max_delay_s=11,
        long_pause_every=10,
        long_pause_s=(30, 90),
        max_details_per_hour=40,
        max_details_per_day=300,
        cooldown_hours_on_block=48,
    ),
    "jobright": SourceLimits(),
    "handshake": SourceLimits(),
}


class SourceConfig(BaseModel):
    """What to search on one source (a plugin, by name) and how fast."""

    model_config = ConfigDict(extra="forbid")
    searches: list[SourceSearch] = []
    limits: SourceLimits | None = None

    @model_validator(mode="after")
    def _unique_names(self):
        names = [s.search_name for s in self.searches]
        dup = sorted({n for n in names if names.count(n) > 1})
        if dup:
            raise ValueError(f"search names must be unique within a source; repeated: {dup}")
        return self


class Defaults(BaseModel):
    model_config = ConfigDict(extra="forbid")
    posted_within_days: PostedWithin = 7
    max_pages_per_search: int = 5
    early_stop_known_streak: int = 15
    card_filters: CardFilters = CardFilters()


class RunWindow(BaseModel):
    start: str = "08:00"
    end: str = "23:00"


class RetryConfig(BaseModel):
    max_fetch_attempts: int = 3
    retry_within_days: int = 7
    # The retry pass may use at most this share of a site's hourly limit, so searches always run.
    max_share_of_hourly: float = 0.5


class RetentionConfig(BaseModel):
    """Posting-data retention: what `auto-app data purge` drops from live postings (docs/step1-collect.md §9.3).
    Whole jobs expire by `jobs.ttl_days` instead."""

    model_config = ConfigDict(extra="forbid")
    raw_days: int = 7  # raw pages/records/screenshots, this long after a successful parse
    sightings_days: int = 30  # search results (card sightings)
    logs_days: int = 30  # run logs and block artifacts

    @model_validator(mode="before")
    @classmethod
    def _retired(cls, data: Any) -> Any:
        """`description_days` / `keep_description_statuses` were replaced by the job TTL (C11); ignored."""
        if isinstance(data, dict):
            data = {k: v for k, v in data.items() if k not in ("description_days", "keep_description_statuses")}
        return data


class JobTTL(BaseModel):
    """Days since a job's postings were last seen before it expires (docs/step1-collect.md §9.1)."""

    model_config = ConfigDict(extra="forbid")
    unfiltered: Annotated[int, Field(ge=1)] = 14  # not evaluated by Step 2 yet → deleted
    un_recommended: Annotated[int, Field(ge=1)] = 7  # Step 2 left it out → deleted
    recommended: Annotated[int, Field(ge=1)] = 30  # undecided, applied or not applied → archived

    def for_state(self, step2: str | None) -> int:
        return {"recommended": self.recommended, "un_recommended": self.un_recommended}.get(step2 or "", self.unfiltered)


class ModelPrice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    input_per_mtok: float  # USD per million input tokens
    output_per_mtok: float  # USD per million output tokens


class SelectConfig(BaseModel):
    """Step 2 runs (docs/step2-select.md). Prices are for cost tracking and `select --max-cost`; check
    https://www.anthropic.com/pricing and add the models your filters use."""

    model_config = ConfigDict(extra="forbid")
    # auto = the Claude API when ANTHROPIC_API_KEY is set, otherwise your Claude Code login (`claude` CLI)
    runner: Literal["auto", "api", "claude-cli"] = "auto"
    claude_cli: str = "claude"  # the Claude Code executable
    deepseek_base_url: str = "https://api.deepseek.com"  # filters with a `deepseek-…` model use DEEPSEEK_API_KEY
    parallel: Annotated[int, Field(ge=1, le=16)] = 3  # agent runs at once
    prices: dict[str, ModelPrice] = Field(default_factory=lambda: {
        "claude-haiku-4-5-20251001": ModelPrice(input_per_mtok=1.0, output_per_mtok=5.0),
    })
    web_search_usd_per_1000: float = 10.0
    # evaluation (docs/step2-select.md §7.4–7.7)
    noise_limit: Annotated[float, Field(gt=0, lt=1)] = 0.05  # unlabelled "no" jobs may train a model below this noise
    audit_size: Annotated[int, Field(ge=1)] = 30  # jobs per filter drawn by `filter audit`
    audit_more: Annotated[int, Field(ge=1)] = 10  # jobs per filter drawn by `d` in the app

    def cost(self, model: str, input_tokens: int, output_tokens: int, web_searches: int) -> float | None:
        """USD, or None when the model has no price configured."""
        price = self.prices.get(model)
        if price is None:
            return None
        return (input_tokens * price.input_per_mtok + output_tokens * price.output_per_mtok) / 1e6 \
            + web_searches * self.web_search_usd_per_1000 / 1000


class JobsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ttl_days: JobTTL = JobTTL()


class UiConfig(BaseModel):
    """The full-screen app (`job browse`)."""

    model_config = ConfigDict(extra="forbid")
    theme: str = "auto"  # auto = follow the system's light / dark mode (live); or any Textual theme name
    light_theme: str = "textual-light"  # what `auto` uses in light mode
    dark_theme: str = "textual-dark"  # … and in dark mode


class AppConfig(BaseModel):
    defaults: Defaults = Defaults()
    sources: dict[str, SourceConfig] = Field(default_factory=dict)  # plugin name → its searches and limits
    run_window: RunWindow = RunWindow()
    min_hours_between_runs: float = 6
    retry: RetryConfig = RetryConfig()
    retention: RetentionConfig = RetentionConfig()
    jobs: JobsConfig = JobsConfig()
    select: SelectConfig = SelectConfig()
    ui: UiConfig = UiConfig()
    # Also write every posting/batch as files under data/raw/ (a browsable debug copy of the database).
    debug_files: bool = False
    # Old `companies:` entries (platform, board) — only to tell you which plugin file to create.
    legacy_companies: dict[str, dict] = Field(default_factory=dict, exclude=True)

    @model_validator(mode="before")
    @classmethod
    def _migrate_old_layouts(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        return _migrate(dict(data))

    def limits(self, source: str, plugin: type | None = None) -> SourceLimits:
        """Built-in defaults for the source (or its plugin's), with configured fields applied on top."""
        base = DEFAULT_LIMITS.get(source) or getattr(plugin, "default_limits", None) or SourceLimits()
        sc = self.sources.get(source)
        override = sc.limits if sc else None
        return base.model_copy(update=override.model_dump(exclude_unset=True)) if override else base


def _migrate(data: dict) -> dict:
    """Older layouts → `sources:`.

    1. top-level `searches` (with `sources: [...]`, `site_urls`) + top-level `sources` holding limits;
    2. `job_sites: {searches, limits}` + `companies: {key: {platform, board, display_name, search(es), search_from, limits}}`.
    """
    if "searches" in data:  # layout 1 → layout 2
        job_sites = dict(data.get("job_sites") or {})
        searches = list(job_sites.get("searches") or [])
        for s in data.pop("searches") or []:
            s = dict(s)
            if "sources" in s:
                s["sites"] = s.pop("sources")
            if "site_urls" in s:
                s["site_url"] = s.pop("site_urls")
            searches.append(s)
        job_sites["searches"] = searches
        limits = dict(job_sites.get("limits") or {})
        limits.update(data.pop("sources", None) or {})
        job_sites["limits"] = limits
        data["job_sites"] = job_sites
    if "job_sites" not in data and "companies" not in data:
        return data
    sources: dict[str, dict] = {k: dict(v or {}) for k, v in (data.get("sources") or {}).items()}

    def add(source: str, search: dict) -> None:
        sources.setdefault(source, {}).setdefault("searches", []).append(search)

    job_sites = data.pop("job_sites", None) or {}
    site_searches = list(job_sites.get("searches") or [])
    for s in site_searches:
        s = dict(s)
        sites, urls = s.pop("sites", []), s.pop("site_url", None) or {}
        for site in sites:
            add(site, {**s, **({"site_url": urls[site]} if site in urls else {})})
    for site, lim in (job_sites.get("limits") or {}).items():
        sources.setdefault(site, {})["limits"] = lim
    legacy = {}
    for key, c in (data.pop("companies", None) or {}).items():
        c = dict(c or {})
        legacy[key] = {k: c.pop(k, None) for k in ("platform", "board", "display_name")}
        if c.get("limits"):
            sources.setdefault(key, {})["limits"] = c["limits"]
        own = [c["search"]] if c.get("search") else list(c.get("searches") or [])
        for i, s in enumerate(own):
            s = dict(s)
            s["name"] = s.get("name") or ("main" if c.get("search") else str(i + 1))
            add(key, s)
        if not own:
            names = c.get("search_from")
            for s in site_searches:
                if (names is None or s.get("name") in names) and s.get("sites"):
                    add(key, {"from": f"{s['sites'][0]}/{s['name']}"})
        sources.setdefault(key, {})
    data["sources"] = sources
    data["legacy_companies"] = legacy
    return data


# ---- global settings: .env / environment (docs: README "Configuration") ----------------------------

GLOBAL_FIELDS = ("run_window", "min_hours_between_runs", "retry", "retention", "jobs", "select", "ui", "debug_files")
ENV_PREFIX = "AUTO_APP_"


class GlobalSettings(BaseSettings):
    """Global settings from the environment (the project's `.env` is loaded into it at startup):
    AUTO_APP_<FIELD>, nested with `__` (AUTO_APP_JOBS__TTL_DAYS__UNFILTERED=14); maps and lists as JSON."""

    model_config = SettingsConfigDict(env_prefix=ENV_PREFIX, env_nested_delimiter="__", extra="ignore")
    run_window: RunWindow | None = None
    min_hours_between_runs: float | None = None
    retry: RetryConfig | None = None
    retention: RetentionConfig | None = None
    jobs: JobsConfig | None = None
    select: SelectConfig | None = None
    ui: UiConfig | None = None
    debug_files: bool | None = None


def load_env_file(path: Path) -> bool:
    """Load a `.env` file into the environment; variables already set in the shell win. True if it exists."""
    from dotenv import load_dotenv

    return load_dotenv(path, override=False) if path.is_file() else False


def env_settings() -> dict:
    """Only the global settings actually set in the environment, as nested dicts."""
    return GlobalSettings().model_dump(exclude_unset=True, exclude_none=True)


def _deep_merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in over.items():
        out[k] = _deep_merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def load_config(path: Path | None) -> AppConfig:
    """searches.yaml (what to search) + global settings from the environment / `.env`, which win.
    Global settings still written in searches.yaml are used, with a notice to move them to `.env`."""
    data = (yaml.safe_load(path.read_text()) or {}) if path and path.is_file() else {}
    in_yaml = [k for k in GLOBAL_FIELDS if k in data]
    if in_yaml:
        log.warning("global settings in %s: %s — move them to .env (see .env.example); .env values win",
                    path.name, ", ".join(in_yaml))
    return AppConfig.model_validate(_deep_merge(data, env_settings()))
