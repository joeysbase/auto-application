"""The apply policy (docs/step3-apply.md §1): `config/apply.yaml` — how the want-to-apply queue is worked.

Workers and pace, the order of the queue, each platform's applier chain, and review / auto submission per platform.
Missing file → the defaults below (the same as config/apply.example.yaml).
"""

import re
from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

PLATFORMS = ("greenhouse", "lever", "ashby", "workday", "linkedin_easy_apply", "other")
DEFAULT_CHAINS = {  # the browser agent first (docs/step3-agent.md) — after Greenhouse's API applier on Greenhouse;
    # simplify_auto stays available (name it in config/apply.yaml); LinkedIn Easy Apply is never automated
    "greenhouse": ["greenhouse", "agent", "simplify", "manual"],
    "lever": ["agent", "lever", "simplify", "manual"],
    "ashby": ["agent", "ashby", "simplify", "manual"],
    "workday": ["agent", "simplify", "manual"],
    "linkedin_easy_apply": ["simplify", "manual"],
    "*": ["agent", "simplify", "manual"],
}
NEVER_AUTOMATED = {"linkedin_easy_apply": {"simplify", "manual"}}  # LinkedIn bans accounts for automated applying


class Pace(BaseModel):
    model_config = ConfigDict(extra="forbid")
    per_hour: Annotated[int, Field(ge=1)] = 6
    per_day: Annotated[int, Field(ge=1)] = 20
    hours: str = "09:00-18:00"  # submissions only in this window (local time)
    per_company_per_month: Annotated[int, Field(ge=1)] = 3

    @field_validator("hours")
    @classmethod
    def _window(cls, v: str) -> str:
        if not re.fullmatch(r"\d{2}:\d{2}-\d{2}:\d{2}", v):
            raise ValueError('expected "HH:MM-HH:MM"')
        return v


class ApplyPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workers: Annotated[int, Field(ge=1, le=16)] = 3
    pace: Pace = Pace()
    order: Literal["queued", "newest_posting", "by_filter"] = "queued"
    appliers: dict[str, list[str]] = Field(default_factory=lambda: dict(DEFAULT_CHAINS))
    submit: dict[str, Literal["review", "auto"]] = Field(default_factory=lambda: {
        "greenhouse": "review", "lever": "review", "ashby": "review"})
    auto_drafts: dict[str, bool] = Field(default_factory=dict)
    notify: bool = True

    def chain(self, platform: str) -> list[str]:
        return list(self.appliers.get(platform) or self.appliers.get("*") or ["manual"])

    def submit_mode(self, applier: str) -> str:
        return self.submit.get(applier, "review")


def check_policy(policy: ApplyPolicy, known: set[str], planned: set[str]) -> list[str]:
    """Problems: unknown appliers or platforms, chains without a manual fallback, LinkedIn automation."""
    problems = []
    for platform, chain in policy.appliers.items():
        if platform != "*" and platform not in PLATFORMS:
            problems.append(f"appliers.{platform}: unknown platform (known: {', '.join(PLATFORMS)}, or \"*\")")
        for name in chain:
            if name not in known | planned:
                problems.append(f"appliers.{platform}: unknown applier {name!r} (known: {', '.join(sorted(known | planned))})")
        allowed = NEVER_AUTOMATED.get(platform)
        if allowed and set(chain) - allowed:
            problems.append(f"appliers.{platform}: only {sorted(allowed)} — LinkedIn Easy Apply is never automated")
        if chain and chain[-1] != "manual":
            problems.append(f"appliers.{platform}: should end with `manual`, so a job never runs out of appliers")
    return problems


def load_policy(path: Path) -> tuple[ApplyPolicy, str | None]:
    """(policy, problem). A missing file gives the defaults; a broken one gives the defaults and the problem."""
    if not path.is_file():
        return ApplyPolicy(), None
    try:
        return ApplyPolicy.model_validate(yaml.safe_load(path.read_text()) or {}), None
    except (yaml.YAMLError, ValidationError) as e:
        return ApplyPolicy(), f"{path.name}: {e}"


def format_policy(policy: ApplyPolicy, known: set[str], planned: set[str]) -> str:
    p = policy.pace
    out = [f"workers {policy.workers} · pace {p.per_hour}/hour, {p.per_day}/day, {p.hours}, "
           f"{p.per_company_per_month}/company/month · order {policy.order} · notify {'on' if policy.notify else 'off'}",
           "applier chains (tried in order; ✓ built, … not built yet — skipped):"]
    for platform, chain in policy.appliers.items():
        out.append(f"  {platform:<20} " + " → ".join(f"{a} {'✓' if a in known else '…'}" for a in chain))
    out.append("submit: " + ", ".join(f"{a} {m}" for a, m in sorted(policy.submit.items())) + " (others: review)")
    return "\n".join(out)
