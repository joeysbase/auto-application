"""How a plugin supports each generic search field (docs/step1-collect.md §2.5).

    Native — the site applies it (URL or API parameter)
    Local  — the engine/plugin applies it to what the site returns
    (not declared) — deferred: applied by Step 2 after parsing
"""

from dataclasses import dataclass, field
from typing import Callable

from pydantic import BaseModel, ConfigDict


@dataclass(frozen=True)
class Native:
    param: str = ""
    values: dict[str, str] = field(default_factory=dict)  # generic value → site value
    note: str = ""
    level = "native"


@dataclass(frozen=True)
class Local:
    fields: tuple[str, ...] = ()
    note: str = ""
    level = "local"


Filter = Native | Local


class NoSiteFilters(BaseModel):
    model_config = ConfigDict(extra="forbid")


def level_of(filters: dict[str, Filter], name: str) -> str:
    f = filters.get(name)
    return f.level if f else "deferred"


def describe(f: Filter | None) -> str:
    if f is None:
        return "deferred (Step 2)"
    detail = f.param if isinstance(f, Native) else ", ".join(f.fields)
    extra = f" — {f.note}" if f.note else ""
    return f"{f.level}" + (f" ({detail})" if detail else "") + extra


def unmapped_values(f: Filter | None, values: list[str]) -> list[str]:
    if isinstance(f, Native) and f.values:
        return [v for v in values if v not in f.values]
    return []


ToValue = Callable[[int], str]
