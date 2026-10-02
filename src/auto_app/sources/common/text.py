"""Text helpers for parsers: HTML → text, salaries, relative dates, common vocabularies."""

import html as htmllib
import re
from datetime import datetime, timedelta, timezone

from auto_app.core.models import Salary

_BLOCK = re.compile(r"</?(p|div|br|li|ul|ol|h[1-6]|tr|table|section|article)\b[^>]*>", re.I)
_LI = re.compile(r"<li\b[^>]*>", re.I)
_TAG = re.compile(r"<[^>]+>")


def html_to_text(value: str | None) -> str:
    """HTML (possibly entity-escaped, as Greenhouse sends it) → readable plain text with line breaks."""
    if not value:
        return ""
    s = value
    if "&lt;" in s and "<" not in s:
        s = htmllib.unescape(s)
    s = _LI.sub("\n- ", s)
    s = _BLOCK.sub("\n", s)
    s = htmllib.unescape(_TAG.sub("", s)).replace("\xa0", " ")
    return clean_lines(s)


def clean_lines(s: str) -> str:
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in s.splitlines()]
    out: list[str] = []
    for line in lines:
        if line or (out and out[-1]):
            out.append(line)
    return "\n".join(out).strip()


# ---- salary ------------------------------------------------------------------------------

_PERIOD = {"yr": "year", "year": "year", "annually": "year", "annual": "year", "hr": "hour", "hour": "hour",
           "hourly": "hour", "mo": "month", "month": "month", "wk": "week", "week": "week"}
_AMOUNT = r"\$\s?([\d,]+(?:\.\d+)?)\s*([kKmM])?"
_SALARY = re.compile(
    _AMOUNT + r"\s*(?:/\s*([a-zA-Z]+))?\s*(?:-|–|—|to)\s*" + _AMOUNT + r"\s*(?:/\s*([a-zA-Z]+)|per\s+([a-zA-Z]+))?"
)
_SINGLE = re.compile(_AMOUNT + r"\s*(?:/\s*([a-zA-Z]+)|per\s+([a-zA-Z]+))")
_USD = r"([\d,]+(?:\.\d+)?)\s*([kK])?\s*USD"
_USD_RANGE = re.compile(_USD + r"\s*(?:-|–|—|to)\s*" + _USD + r"(?:\s*(?:per|/)\s*([a-zA-Z]+))?")


def _num(value: str, suffix: str | None) -> float:
    n = float(value.replace(",", ""))
    if suffix:
        n *= 1_000 if suffix.lower() == "k" else 1_000_000
    return n


def parse_salary(text: str | None, source: str) -> Salary | None:
    s = _parse_salary(text, source)
    if s and s.max is not None:
        s.plausible = not ((s.period == "year" and s.max < 1_000) or (s.period == "hour" and s.min and s.min > 1_000))
    return s


def _parse_salary(text: str | None, source: str) -> Salary | None:
    if not text or ("$" not in text and "USD" not in text):
        return None
    m = _USD_RANGE.search(text)  # "116,000 USD - 184,000 USD" (Workday descriptions)
    if m:
        lo, hi = _num(m.group(1), m.group(2)), _num(m.group(3), m.group(4))
        unit = (m.group(5) or "").lower()
        return Salary(min=lo, max=hi, currency="USD", period=_PERIOD.get(unit) or _guess_period(hi),
                      text=m.group(0).strip(), source=source)
    m = _SALARY.search(text)
    if m:
        lo, hi = _num(m.group(1), m.group(2)), _num(m.group(4), m.group(5))
        unit = (m.group(3) or m.group(6) or m.group(7) or "").lower()
        return Salary(min=lo, max=hi, currency="USD", period=_PERIOD.get(unit) or _guess_period(hi),
                      text=m.group(0).strip(), source=source)
    m = _SINGLE.search(text)
    if m:
        v = _num(m.group(1), m.group(2))
        unit = (m.group(3) or m.group(4) or "").lower()
        return Salary(min=v, max=v, currency="USD", period=_PERIOD.get(unit) or _guess_period(v),
                      text=m.group(0).strip(), source=source)
    return None


def _guess_period(amount: float) -> str | None:
    if amount >= 10_000:
        return "year"
    if amount <= 500:
        return "hour"
    return None


SALARY_HINT = re.compile(r"salary|compensation|pay range|base pay|pay rate|hourly rate|wage", re.I)


def salary_from_description(text: str) -> Salary | None:
    """A pay range stated in the description, only on lines that talk about pay."""
    for line in text.splitlines():
        if SALARY_HINT.search(line) and ("$" in line or "USD" in line):
            s = parse_salary(line, "description")
            if s:
                return s
    return None


# ---- dates ---------------------------------------------------------------------------------

_REL = re.compile(r"(\d+)\s+(second|minute|hour|day|week|month|year)s?\s+ago", re.I)
_UNIT = {"second": 1 / 86400, "minute": 1 / 1440, "hour": 1 / 24, "day": 1, "week": 7, "month": 30, "year": 365}


def relative_time(text: str | None, ref: datetime) -> datetime | None:
    """'12 minutes ago', 'Reposted 2 weeks ago', 'yesterday', 'just now' → datetime (approximate)."""
    if not text:
        return None
    t = text.lower()
    if "just now" in t or "today" in t or "moments ago" in t:
        return ref
    if "yesterday" in t:
        return ref - timedelta(days=1)
    m = _REL.search(t)
    if m:
        return ref - timedelta(days=int(m.group(1)) * _UNIT[m.group(2)])
    return None


def as_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


# ---- vocabularies ----------------------------------------------------------------------------

WORKPLACE = {"on-site": "onsite", "onsite": "onsite", "on site": "onsite", "in office": "onsite",
             "remote": "remote", "hybrid": "hybrid"}
EMPLOYMENT = {"full-time": "full_time", "full time": "full_time", "fulltime": "full_time",
              "part-time": "part_time", "part time": "part_time", "parttime": "part_time",
              "contract": "contract", "contractor": "contract", "temporary": "temporary", "temp": "temporary",
              "internship": "internship", "intern": "internship"}
SENIORITY = {"internship", "entry level", "associate", "mid-senior level", "director", "executive"}


def workplace_of(value: str | None) -> str:
    return WORKPLACE.get((value or "").strip().lower(), "unknown")


def employment_of(value: str | None) -> str:
    return EMPLOYMENT.get((value or "").strip().lower(), "unknown")
