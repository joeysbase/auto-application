"""A search with every setting resolved (defaults applied), as handed to plugins."""

from dataclasses import dataclass, field
from typing import Any

from auto_app.core.config import CardFilters


@dataclass
class ResolvedSearch:
    name: str  # recorded as search_runs.search_name
    keywords: list[str]
    locations: list[str]
    posted_within_days: int | None  # None = no date limit ("all")
    workplace_types: list[str]
    employment_types: list[str]
    experience_levels: list[str]
    max_pages: int
    known_streak: int
    card_filters: CardFilters
    site_filters: Any = None  # the plugin's SiteFilters instance (or None)
    site_url: str | None = None  # browser: page-1 URL copied from the browser
    levels: dict[str, str] = field(default_factory=dict)  # field → native | local | deferred

    def active(self, name: str) -> bool:
        value = getattr(self, name)
        return bool(value) if isinstance(value, list) else value is not None

    @property
    def deferred(self) -> list[str]:
        return [f for f, lvl in self.levels.items() if lvl == "deferred"]

    def record(self) -> dict:
        """What gets stored with each search run."""
        site_filters = self.site_filters.model_dump(exclude_defaults=True) if self.site_filters is not None else {}
        return {
            "keywords": self.keywords,
            "locations": self.locations,
            "posted_within_days": self.posted_within_days,
            "workplace_types": self.workplace_types,
            "employment_types": self.employment_types,
            "experience_levels": self.experience_levels,
            "site_filters": site_filters,
            "levels": self.levels,
            "deferred": self.deferred,
        }
