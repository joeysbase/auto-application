"""Parser interface pieces shared by plugins (docs/step1-collect.md §5)."""

import re
from datetime import datetime

from auto_app.core.models import CommonPosting, RawPosting
from auto_app.sources.common.text import (
    as_utc,
    employment_of,
    html_to_text,
    parse_salary,
    salary_from_description,
)


class ParseError(ValueError):
    """The raw data doesn't contain what the parser needs (raw is kept; fix the parser and re-parse)."""


def jsonld_jobposting(embedded: dict) -> dict | None:
    """The first schema.org JobPosting in a page's JSON-LD blocks."""
    for item in embedded.get("jsonld") or []:
        for node in item if isinstance(item, list) else [item]:
            if not isinstance(node, dict):
                continue
            graph = node.get("@graph")
            for n in graph if isinstance(graph, list) else [node]:
                if isinstance(n, dict) and n.get("@type") in ("JobPosting", ["JobPosting"]):
                    return n
    return None


def _jsonld_locations(jp: dict) -> list[str]:
    locs = jp.get("jobLocation") or []
    out = []
    for loc in locs if isinstance(locs, list) else [locs]:
        addr = (loc or {}).get("address") or {}
        if isinstance(addr, dict):
            parts = [addr.get(k) for k in ("addressLocality", "addressRegion", "addressCountry")]
            parts = [p.get("name") if isinstance(p, dict) else p for p in parts]
            out.append(", ".join(p for p in parts if p))
    if jp.get("jobLocationType") == "TELECOMMUTE":
        out.append("Remote")
    return [x for x in out if x]


def parse_jsonld(raw: RawPosting) -> CommonPosting:
    """Default browser parser: schema.org JobPosting embedded in the page (many career sites have it)."""
    jp = jsonld_jobposting(raw.embedded)
    if not jp:
        raise ParseError("no JSON-LD JobPosting on the page and no site-specific parser")
    org = jp.get("hiringOrganization") or {}
    desc_html = jp.get("description") or ""
    desc = html_to_text(desc_html)
    salary = None
    base = jp.get("baseSalary") or {}
    value = base.get("value") if isinstance(base, dict) else None
    if isinstance(value, dict) and (value.get("minValue") or value.get("value")):
        from auto_app.core.models import Salary

        salary = Salary(min=value.get("minValue") or value.get("value"), max=value.get("maxValue") or value.get("value"),
                        currency=base.get("currency"), period=(value.get("unitText") or "").lower() or None,
                        source="structured")
    et = jp.get("employmentType")
    et = et[0] if isinstance(et, list) and et else et
    posted = jp.get("datePosted")
    return CommonPosting(
        source=raw.source,
        source_job_id=raw.source_job_id,
        title=(jp.get("title") or raw.card.get("title") or "").strip(),
        company=org.get("name") if isinstance(org, dict) else None,
        locations=_jsonld_locations(jp),
        employment_type=employment_of(str(et).replace("_", "-")) if et else "unknown",
        salary=salary or salary_from_description(desc),
        posted_at=as_utc(datetime.fromisoformat(posted)) if posted else None,
        posted_at_precision="day" if posted else None,
        apply_url=raw.capture.get("apply_href"),
        apply_kind=raw.capture.get("apply_kind", "unknown"),
        url=raw.capture.get("final_url"),
        description_text=desc,
        description_html=desc_html or None,
    )


REQ_ID = re.compile(r"\b(?:job\s*(?:req(?:uisition)?)?\s*id|req(?:uisition)?\s*(?:id|#|number))\s*[:#]?\s*([A-Z0-9][A-Z0-9-]{3,})",
                    re.I)


def req_id_from_text(text: str) -> str | None:
    m = REQ_ID.search(text or "")
    return m.group(1) if m else None


def require_title(p: CommonPosting) -> CommonPosting:
    if not p.title:
        raise ParseError("no title found")
    return p


__all__ = ["ParseError", "parse_jsonld", "jsonld_jobposting", "req_id_from_text", "require_title", "parse_salary"]
