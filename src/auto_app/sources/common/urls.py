"""Employer job keys: the same job's identity on its employer platform, parsed from any URL
that points at it — a LinkedIn apply link or a company posting's own URL (docs/step1-collect.md §6).

    https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite/job/US-TX-Austin/Title_JR2026343?source=x
        → workday / nvidia/nvidiaexternalcareersite / JR2026343
    https://jobs.ashbyhq.com/seqholdings/91eed659-…/application?utm_source=…  → ashby / seqholdings / 91eed659-…
    https://boards.greenhouse.io/spacex/jobs/8854394002?gh_jid=…              → greenhouse / * / 8854394002
    https://jobs.lever.co/palantir/6ed76ce8-…/apply                           → lever / palantir / 6ed76ce8-…
"""

import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

LOCALE = re.compile(r"^[a-z]{2}-[A-Z]{2}$")
# Workday job segment "<Title-Slug>_<ID>"; IDs look like JR2026343, REQ-3930, R24265, REQ_0000082877-1
WORKDAY_ID = re.compile(r"_((?:[A-Za-z]{1,6}[_-]?)?\d[\w-]*)$")
UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


@dataclass(frozen=True)
class EmployerKey:
    platform: str
    board: str  # "*" when the platform's job ids are global (Greenhouse)
    job_id: str

    @property
    def key(self) -> str:
        return f"{self.platform}:{self.board}:{self.job_id}"


def workday_job_id(segment: str) -> str:
    """The id at the end of a Workday job path segment (the whole segment if none is recognizable)."""
    m = WORKDAY_ID.search(segment)
    return m.group(1) if m else segment


def employer_job_key(url: str | None) -> EmployerKey | None:
    if not url:
        return None
    u = urlparse(url if "://" in url else "https://" + url)
    host = (u.hostname or "").lower()
    parts = [p for p in u.path.split("/") if p]
    query = parse_qs(u.query)

    if host.endswith(".myworkdayjobs.com"):
        tenant = host.split(".")[0]
        parts = [p for p in parts if not LOCALE.match(p)]
        if len(parts) >= 3 and "job" in parts:
            site = parts[0]
            return EmployerKey("workday", f"{tenant}/{site.lower()}", workday_job_id(parts[-1]))
        return None

    if host in ("jobs.ashbyhq.com",) and len(parts) >= 2 and UUID.match(parts[1]):
        return EmployerKey("ashby", parts[0].lower(), parts[1].lower())

    if host.endswith("greenhouse.io"):
        if "jobs" in parts:
            i = parts.index("jobs")
            if i + 1 < len(parts) and parts[i + 1].isdigit():
                return EmployerKey("greenhouse", "*", parts[i + 1])
    gh = (query.get("gh_jid") or [None])[0]
    if gh and gh.isdigit():  # Greenhouse embedded on a company domain: …/careers?gh_jid=123
        return EmployerKey("greenhouse", "*", gh)

    if host in ("jobs.lever.co", "jobs.eu.lever.co") and len(parts) >= 2 and UUID.match(parts[1]):
        return EmployerKey("lever", parts[0].lower(), parts[1].lower())
    return None
