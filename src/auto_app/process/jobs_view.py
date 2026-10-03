"""`auto-app job list` / `job show`: the job list and one job's details, sized to the terminal."""

import shutil
from datetime import datetime, timezone

SMALL_WORDS = {"of", "and", "the", "in", "at", "on", "for", "de", "la"}
RULES = {  # how a posting joined its job, in words
    "new_job": "first posting of this job",
    "employer_key": "same job id on the employer's system",
    "req_id": "same company + requisition id",
    "description": "same company and title, matching description",
    "desc_hash": "identical description",
    "probable": "probably the same (same company + title, a description missing)",
    "group": "grouped with the postings above",
}


def place(loc: str) -> str:
    """A normalized location → display form: "austin, tx" → "Austin, TX"; "remote-us" → "Remote (US)"."""
    if loc in ("us", "usa", "united states"):
        return "United States"
    if loc.startswith("remote"):
        rest = loc.removeprefix("remote").strip(" -,")
        return f"Remote ({rest.upper()})" if rest else "Remote"
    parts = [p.strip() for p in loc.split(",")]
    out = []
    for part in parts:
        if len(part) == 2:
            out.append(part.upper())  # state / country code
        else:
            words = part.split()
            out.append(" ".join(w if (j and w in SMALL_WORDS) else w[:1].upper() + w[1:] for j, w in enumerate(words)))
    return ", ".join(out)


def places(locs: list[str], width: int) -> str:
    shown = [place(x) for x in locs]
    if not shown:
        return "—"
    text = shown[0]
    for i, nxt in enumerate(shown[1:], start=1):
        more = len(shown) - i
        if len(text) + len(nxt) + 2 + len(f" +{more - 1}") > width:
            return f"{text} +{more}"
        text += f"; {nxt}"
    return text


def _date(value: str | None, long: bool = False) -> str:
    if not value:
        return "—"
    try:
        d = datetime.fromisoformat(value)
    except ValueError:
        return value[:10]
    return d.strftime("%Y-%m-%d") if long else d.strftime("%b %d")


def _cut(text: str | None, width: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= width else text[: max(1, width - 1)] + "…"


STATE_LABEL = {"not_evaluated": "not evaluated", "un_recommended": "un-recommended", "recommended": "recommended",
               "applied": "applied", "not_applied": "not applied", "archived": "archived"}
WARN_DAYS = 3  # recommended, undecided jobs this close to being archived are flagged


def _days_left(j: dict, now: datetime | None = None) -> int | None:
    if not j.get("expires_at"):
        return None
    exp = datetime.fromisoformat(j["expires_at"])
    now = now or datetime.now(timezone.utc)
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    return (exp - now).days


def expiry(j: dict, now: datetime | None = None) -> str:
    """"in 12d" / "today" / "due" (the next purge removes or archives it); archived jobs: the archive date."""
    if j.get("archived_at"):
        return _date(j["archived_at"])
    left = _days_left(j, now)
    if left is None:
        return "—"
    text = "due" if left < 0 else "today" if left == 0 else f"in {left}d"
    if j.get("state") == "recommended" and left <= WARN_DAYS:
        text += "!"
    return text


def _newest_first(j: dict):
    return (j.get("posted_at") or j.get("first_seen_at") or "", j["job_id"])


def format_job_list(rows: list[dict], width: int | None = None) -> str:
    width = width or shutil.get_terminal_size((140, 40)).columns
    rows = sorted(rows, key=_newest_first, reverse=True)
    show_status = len({j["state"] for j in rows}) > 1
    company_w = min(22, max(8, max(len(j.get("company") or j.get("company_norm") or "") for j in rows)))
    where_w = 24 if width >= 120 else 16
    src_w = min(18, max(7, max(len(", ".join(j["sources"])) for j in rows)))
    status_w = 14 if show_status else 0
    exp_w = 8
    fixed = 4 + 1 + 6 + 2 + company_w + 2 + 2 + where_w + 2 + src_w + 2 + exp_w + (status_w + 2 if show_status else 0)
    title_w = max(24, width - fixed - 1)

    multi = sum(1 for j in rows if len(j["sources"]) > 1)
    head = f"{len(rows)} jobs" + (f" · {multi} found on more than one source" if multi else "") + " · newest first"
    cols = (f"{'#':>4} {'posted':<6}  {'company':<{company_w}}  {'title':<{title_w}}  {'where':<{where_w}}  "
            f"{'sources':<{src_w}}  {'expires':<{exp_w}}" + (f"  {'state':<{status_w}}" if show_status else ""))
    if any(j.get("archived_at") for j in rows):
        cols = cols.replace(f"{'expires':<{exp_w}}", f"{'archived':<{exp_w}}")
    out = [head, "", cols, "─" * min(width - 1, len(cols))]
    for j in rows:
        title = ("⚠ " if j.get("warnings") else "") + (j.get("title") or "")
        line = (f"{j['job_id']:>4} {_date(j.get('posted_at')):<6}  "
                f"{_cut(j.get('company') or j.get('company_norm'), company_w):<{company_w}}  "
                f"{_cut(title, title_w):<{title_w}}  {places(j['locations'], where_w):<{where_w}}  "
                f"{_cut(', '.join(j['sources']), src_w):<{src_w}}  {expiry(j):<{exp_w}}")
        if show_status:
            line += f"  {STATE_LABEL.get(j['state'], j['state']):<{status_w}}"
        out.append(line.rstrip())
    notes = ["details of one job: auto-app job show <#>"]
    if any(j.get("warnings") for j in rows):
        notes.insert(0, "⚠ = looks like an archived job you already acted on (see its details)")
    if any(expiry(j).endswith("!") for j in rows):
        notes.insert(0, f"! = recommended, undecided, archived within {WARN_DAYS} days unless seen again")
    out += [""] + notes
    return "\n".join(out)


def _how(p: dict) -> str:
    rule = p.get("match_rule") or ""
    if p.get("duplicate_of"):
        return f"repeat of {p['duplicate_of']} on the same source"
    text = RULES.get(rule, rule)
    if p.get("match_confidence") is not None and rule not in ("new_job", "group"):
        text += f" ({p['match_confidence']:.2f})"
    return text


def _state_line(j: dict) -> str:
    label = STATE_LABEL.get(j["state"], j["state"])
    parts = [label]
    if j.get("step2"):
        parts.append(f"Step 2: {j['step2']} {_date(j.get('step2_at'), long=True)}")
    if j.get("decision"):
        parts.append(f"you: {j['decision']} {_date(j.get('decided_at'), long=True)}")
    return " · ".join(dict.fromkeys(parts))


def _expires_line(j: dict) -> str:
    if j.get("archived_at"):
        return f"archived {_date(j['archived_at'], long=True)} (kept as a record; excluded from dedup)"
    if not j.get("expires_at"):
        return "—"
    what = "archived" if j["state"] in ("recommended", "applied", "not_applied") else "deleted"
    left = _days_left(j)
    when = f"in {left} days" if left and left > 0 else "at the next purge"
    return f"{_date(j['expires_at'], long=True)} — {what} {when} unless a posting is seen again"


def format_job(j: dict, postings: list[dict], keys: list[tuple[str, str]]) -> str:
    out = [f"job #{j['job_id']}  {j.get('title') or ''}", ""]
    rows = [
        ("company", j.get("company") or j.get("company_norm") or "—"),
        ("where", " · ".join(place(x) for x in j["locations"]) or "—"),
        ("posted", _date(j.get("posted_at"), long=True)),
        ("seen", f"{_date(j.get('first_seen_at'), long=True)} → {_date(j.get('last_seen_at'), long=True)}"),
        ("apply", j.get("canonical_url") or (j.get("archive") or {}).get("apply_url") or "—"),
        ("state", _state_line(j)),
        ("expires", _expires_line(j)),
    ]
    out += [f"  {k:<8} {v}" for k, v in rows]
    for w in j.get("warnings") or []:
        out.append(f"  ⚠        {w.get('text')}")
    out += ["", f"postings ({len(postings)}) — the same opening on each source"]
    ref_w = max(len(f"{p['source']}/{p['source_job_id']}") for p in postings) if postings else 10
    for p in postings:
        ref = f"{p['source']}/{p['source_job_id']}"
        out.append(f"  {ref:<{ref_w}}  {(p.get('apply_kind') or ''):<10}  {_how(p)}")
        if p.get("title") and p["title"] != j.get("title"):
            out.append(f"  {'':<{ref_w}}  title: {p['title']}")
    if keys:
        out += ["", "matching keys (how later postings find this job)"]
        out += [f"  {t:<13} {v}" for t, v in keys]
    return "\n".join(out)
