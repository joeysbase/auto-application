"""Appliers (docs/step3-apply.md §3): each knows how to apply to one kind of application form.

    can_apply(app)   — can it handle this application (its platform, its apply link)?
    prepare(app)     — read the form and answer what it can (automated appliers, A2+), or hand the job to you
    open(app)        — hand-off appliers: open the application for you

Built: **greenhouse** (automated: questions from its API, the form filled in a browser — greenhouse.py), and the two
hand-off appliers, which cover every platform: **simplify** (the apply link in your Chrome, where the Simplify
extension autofills it — you submit) and **manual** (the apply link and your CV — you apply). The others (lever,
ashby, workday, generic) are *planned*: a chain naming them skips them ("not built yet") and falls back to the next.
"""

import subprocess
import sys
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

PLANNED = {"lever": "A4", "ashby": "A4", "workday": "A6", "generic": "A7"}


def platform_of(url: str | None) -> str:
    """The application platform from a job's apply link."""
    host = (urlparse(url or "").hostname or "").lower()
    if "greenhouse.io" in host:
        return "greenhouse"
    if host.endswith("lever.co"):
        return "lever"
    if "ashbyhq.com" in host:
        return "ashby"
    if "myworkdayjobs.com" in host or "workday" in host:
        return "workday"
    if host.endswith("linkedin.com"):
        return "linkedin_easy_apply"  # no employer link found: LinkedIn's own (Easy Apply) — never automated
    return "other"


@dataclass
class Outcome:
    kind: str  # handoff | ready | needs_input | action_needed | unsupported | failed | closed | filled | submitted
    reason: str = ""
    questions: list | None = None  # prepare: every question, answered or not
    apply_url: str | None = None  # prepare: the form's own link, when the applier knows a better one


def open_in_chrome(url: str) -> None:
    """Your own Chrome (with your extensions — Simplify — and logins). Falls back to the default browser."""
    if sys.platform == "darwin":
        if subprocess.run(["open", "-a", "Google Chrome", url], capture_output=True).returncode == 0:
            return
    webbrowser.open(url)


def reveal(path: Path) -> None:
    """Show a file in Finder (your CV, to attach)."""
    if sys.platform == "darwin":
        subprocess.run(["open", "-R", str(path)], capture_output=True)


class Applier:
    name = ""
    handoff = False  # you do the applying; the app waits for your "applied" / "couldn't"

    def can_apply(self, app: dict) -> Outcome | None:
        """None = yes; otherwise why not (an `unsupported` outcome)."""
        return None if app.get("apply_url") else Outcome("unsupported", "no apply link")

    def prepare(self, app: dict, ctx=None) -> Outcome:
        raise NotImplementedError


class SimplifyHandoff(Applier):
    """Opens the apply link in your Chrome, where the Simplify extension autofills the form; you check and submit."""

    name = "simplify"
    handoff = True

    def __init__(self, opener=open_in_chrome):
        self.opener = opener

    def prepare(self, app: dict, ctx=None) -> Outcome:
        return Outcome("handoff", "open it in Chrome; Simplify autofills; you submit")

    def open(self, app: dict, cv: Path | None = None) -> str:
        self.opener(app["apply_url"])
        return "opened in Chrome — use Simplify's autofill, check, attach your CV if it asks, submit"


class ManualApplier(Applier):
    """The last resort: the apply link and your CV — you apply by hand."""

    name = "manual"
    handoff = True

    def __init__(self, opener=webbrowser.open, revealer=reveal):
        self.opener, self.revealer = opener, revealer

    def can_apply(self, app: dict) -> Outcome | None:
        return None  # always: without a link you still apply your way, and say so

    def prepare(self, app: dict, ctx=None) -> Outcome:
        return Outcome("handoff", "apply by hand")

    def open(self, app: dict, cv: Path | None = None) -> str:
        if app.get("apply_url"):
            self.opener(app["apply_url"])
        if cv:
            self.revealer(cv)
        return "opened the apply link" + (" and your CV" if cv else "") + " — apply, then press a"


@dataclass
class ApplyContext:
    """What the automated appliers need: your profile and answers, Claude (matcher), the threshold, the browser."""

    me: object
    matcher: object = None
    threshold: float = 0.9
    headless: bool = False
    browser_profile: Path | None = None
    answers_file: Path | None = None  # profile/answers.yaml, for "save" in the inbox
    browser_profiles: Path | None = None  # browser-profiles/ (the Simplify browser's profile lives there)
    simplify_consent: str = "essentials"  # Simplify's one-time consent notice: essentials | accept
    dry_run: bool = True  # starting a job fills the real form headless and reads it back


def registry(opener=None, manual_opener=None, revealer=None, http=None, filler=None, dry_runner=None,
             simplify_context=None, simplify_route=None) -> dict[str, Applier]:
    """The built appliers, by name (openers, the HTTP client, the form filler / dry run and the Simplify browser
    injectable for tests)."""
    from auto_app.apply.greenhouse import GreenhouseApplier
    from auto_app.apply.simplify import SimplifyAutoApplier

    return {"simplify_auto": SimplifyAutoApplier(open_context=simplify_context, route=simplify_route),
            "greenhouse": GreenhouseApplier(http=http, filler=filler, dry_runner=dry_runner),
            "simplify": SimplifyHandoff(**({"opener": opener} if opener else {})),
            "manual": ManualApplier(**{k: v for k, v in (("opener", manual_opener), ("revealer", revealer)) if v})}
