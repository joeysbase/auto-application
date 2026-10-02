"""LinkedIn job page → CommonPosting, from the page's visible text.

LinkedIn's class names are generated (they change), so the parser anchors on stable text:
    <company>
    <title>
    <location> · [Reposted] <N units> ago · <N applicants | N people clicked apply>
    [Promoted by hirer · …] [Responses managed off LinkedIn]
    [$100K/yr - $140K/yr]  [On-site | Remote | Hybrid]  [Full-time | …]
    Apply | Easy Apply | Save | Take the next step … | Application submitted
    …
    About the job
    <description>
    … more | Benefits found in job post | Set alert for similar jobs | About the company
"""

import re
from datetime import datetime

from auto_app.core.models import CommonPosting, RawPosting
from auto_app.sources.common.text import (
    SENIORITY,
    as_utc,
    clean_lines,
    employment_of,
    parse_salary,
    relative_time,
    salary_from_description,
    workplace_of,
)
from auto_app.sources.parsing import ParseError, req_id_from_text

PARSER_VERSION = "linkedin-2026-09-30"

HEADER_END = {"apply", "easy apply", "save", "take the next step in your job search", "application status",
              "application submitted", "i'm interested"}
DESC_END = ("… more", "...more", "show more", "show less", "benefits found in job post", "set alert for similar jobs",
            "about the company", "see how you compare to", "more jobs")
APPLICANTS = re.compile(r"(over\s+)?([\d,]+)\s+(applicants?|people clicked apply|person clicked apply)", re.I)
AGO = re.compile(r"\bago\b|just now", re.I)


def _nonblank_before(lines: list[str], i: int) -> int | None:
    j = i - 1
    while j >= 0 and not lines[j]:
        j -= 1
    return j if j >= 0 else None


def parse_linkedin(raw: RawPosting) -> CommonPosting:
    lines = [line.strip() for line in raw.page_text.splitlines()]
    try:
        about = lines.index("About the job")
    except ValueError:
        raise ParseError("no 'About the job' section (page not loaded, or LinkedIn changed its layout)") from None

    head = lines[:about]
    meta_i = next((i for i in range(len(head) - 1, -1, -1) if " · " in head[i] and AGO.search(head[i])), None)
    card_title = (raw.card.get("title") or "").strip()
    if meta_i is None:
        raise ParseError("no '<location> · <posted> ago' line in the header")
    title_i = _nonblank_before(head, meta_i)
    company_i = _nonblank_before(head, title_i) if title_i is not None else None
    title = head[title_i] if title_i is not None else card_title
    company = head[company_i] if company_i is not None else None

    # "<location> · [Reposted] 3 hours ago · 29 applicants"
    parts = [p.strip() for p in head[meta_i].split("·")]
    location = parts[0] if parts and not AGO.search(parts[0]) else None
    posted_part = next((p for p in parts if AGO.search(p)), None)
    applicants_part = next((p for p in parts if APPLICANTS.search(p)), None)
    fetched_at = as_utc(datetime.fromisoformat(raw.capture["fetched_at"]))
    reposted = bool(posted_part and posted_part.lower().startswith("reposted"))
    posted_at = relative_time(posted_part, fetched_at)
    applicants = None
    if applicants_part:
        m = APPLICANTS.search(applicants_part)
        applicants = int(m.group(2).replace(",", ""))

    # header facts between the meta line and the action buttons
    salary = workplace = employment = seniority = None
    promoted = managed_off_linkedin = False
    for line in head[meta_i + 1:]:
        low = line.lower()
        if low in HEADER_END:
            break
        if not line:
            continue
        if "$" in line and salary is None:
            salary = parse_salary(line, "header")
        elif workplace_of(line) != "unknown":
            workplace = workplace_of(line)
        elif employment_of(line) != "unknown":
            employment = employment_of(line)
        elif low in SENIORITY:
            seniority = line
        elif low.startswith("promoted by"):
            promoted = True
        elif "responses managed off linkedin" in low:
            managed_off_linkedin = True

    # description: "About the job" … first end marker
    desc_lines = []
    for line in lines[about + 1:]:
        if line.lower() in DESC_END:
            break
        desc_lines.append(line)
    description = clean_lines("\n".join(desc_lines))
    if not description:
        raise ParseError("empty description")

    redirects = raw.capture.get("apply_redirects") or []
    apply_url = redirects[-1] if redirects else raw.capture.get("apply_href")
    already_applied = "application submitted" in (line.lower() for line in head)
    return CommonPosting(
        source=raw.source,
        source_job_id=raw.source_job_id,
        title=title,
        company=company,
        locations=[location] if location else [],
        workplace_type=workplace or "unknown",
        employment_type=employment or "unknown",
        seniority=seniority,
        salary=salary or salary_from_description(description),
        posted_at=posted_at,
        posted_at_precision="approx" if posted_at else None,
        posted_text=posted_part,
        reposted=reposted,
        reposted_at=posted_at if reposted else None,
        applicants=applicants,
        applicants_text=applicants_part,
        apply_url=apply_url,
        apply_kind=raw.capture.get("apply_kind", "unknown"),
        url=raw.capture.get("final_url"),
        req_id=req_id_from_text(description),
        description_text=description,
        extras={
            "promoted": promoted,
            "responses_managed_off_linkedin": managed_off_linkedin,
            "already_applied": already_applied,
            "card_title": card_title or None,
        },
    )
