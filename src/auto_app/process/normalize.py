"""Step 1b normalize: parsed fields → keys that dedup matches on (docs/step1-collect.md §6).

parsed → normalized (normalized.json written). Versioned: a posting is normalized again when
NORMALIZER_VERSION changes or when it was re-parsed after its last normalization.
"""

import hashlib
import logging
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from auto_app.core.models import utcnow
from auto_app.process.similarity import minhash, shingles
from auto_app.sources.common.urls import employer_job_key

log = logging.getLogger(__name__)

NORMALIZER_VERSION = "3"

# ---- company ------------------------------------------------------------------------------

COMPANY_SUFFIXES = {
    "inc", "incorporated", "llc", "ltd", "limited", "corp", "corporation", "co", "company", "plc", "gmbh", "ag",
    "sa", "bv", "nv", "lp", "llp", "pc", "pllc", "holdings", "group",
}


def company_basic(name: str | None) -> str:
    """Lowercase, drop parentheticals ("Vendra (YC S24)"), punctuation, and legal suffixes."""
    if not name:
        return ""
    s = re.sub(r"\([^)]*\)", " ", name.lower())
    s = re.sub(r"[^a-z0-9&+ ]+", " ", s).replace("&", " and ")
    words = s.split()
    while words and words[-1] in COMPANY_SUFFIXES:
        words.pop()
    return " ".join(words)


class CompanyAliases:
    """name → company key. Sources: company plugins (name, company, board), learned matches."""

    def __init__(self, companies: dict | None = None, learned: dict[str, str] | None = None):
        """`companies`: source name → PluginInfo (Registry.companies())."""
        self.by_name: dict[str, str] = {}
        self.by_board: dict[tuple[str, str], str] = {}
        self.learned: dict[str, str] = dict(learned or {})
        for key, c in (companies or {}).items():
            for n in (key, c.company):
                if n:
                    self.by_name[company_basic(n)] = key
            board = _config_board(c.platform, c.board)
            if board:
                self.by_board[board] = key

    def resolve(self, name: str | None, ekey, source_company: str | None) -> tuple[str, str]:
        """(company_norm, how) for a posting."""
        if source_company:  # a company source: its source name
            return source_company, "config"
        if ekey and (ekey.platform, ekey.board) in self.by_board:
            key = self.by_board[(ekey.platform, ekey.board)]
            basic = company_basic(name)
            if basic and basic not in self.by_name and self.learned.get(basic) != key:
                self.learned[basic] = key  # e.g. "nvidia corporation" → nvidia
            return key, "board"
        basic = company_basic(name)
        if basic in self.by_name:
            return self.by_name[basic], "alias"
        if basic in self.learned:
            return self.learned[basic], "learned"
        return basic, "name"


def _config_board(platform: str, board: str | None) -> tuple[str, str] | None:
    if not board:
        return None
    if platform == "workday":
        u = re.match(r"(?:https?://)?([a-z0-9-]+)\.wd\d+\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?([^/?#]+)", board)
        return ("workday", f"{u.group(1)}/{u.group(2).lower()}") if u else None
    if platform == "greenhouse":
        return None  # Greenhouse keys are global (board "*"); matched by job id
    return (platform, board.lower())


# ---- title / location / description -------------------------------------------------------------

ABBREVIATIONS = {
    "sr": "senior", "snr": "senior", "jr": "junior", "swe": "software engineer", "sde": "software development engineer",
    "ml": "machine learning", "mle": "machine learning engineer", "eng": "engineer", "engr": "engineer",
    "mgr": "manager", "dev": "developer", "ai/ml": "ai machine learning", "nlp": "natural language processing",
    "cv": "computer vision", "qa": "quality assurance", "ui": "user interface", "ux": "user experience",
    "&": "and",
}
TITLE_NOISE = re.compile(
    r"\((?:remote|on-?site|hybrid|contract|full[- ]time|part[- ]time|us|usa)[^)]*\)|"  # "(Remote)", "(Onsite)"
    r"\|\s*(?:remote|hybrid|on-?site)\b|"  # "| Remote"
    r"\b(?:jr|req|r)[-_ ]?\d{3,}\b",  # requisition numbers in titles
    re.I,
)


def title_norm(title: str | None) -> str:
    if not title:
        return ""
    s = TITLE_NOISE.sub(" ", title.lower())
    s = re.sub(r"[^a-z0-9/&+#. ]+", " ", s)
    words = []
    for w in s.split():
        w = w.strip(".")
        if w in ABBREVIATIONS:
            words.append(ABBREVIATIONS[w])
        else:  # "engineer/ai" → "engineer ai"
            words += [ABBREVIATIONS.get(x, x) for x in w.split("/") if x]
    return re.sub(r"\s+", " ", " ".join(w for w in words if w)).strip()


US_STATES = {
    "alabama": "al", "alaska": "ak", "arizona": "az", "arkansas": "ar", "california": "ca", "colorado": "co",
    "connecticut": "ct", "delaware": "de", "florida": "fl", "georgia": "ga", "hawaii": "hi", "idaho": "id",
    "illinois": "il", "indiana": "in", "iowa": "ia", "kansas": "ks", "kentucky": "ky", "louisiana": "la",
    "maine": "me", "maryland": "md", "massachusetts": "ma", "michigan": "mi", "minnesota": "mn",
    "mississippi": "ms", "missouri": "mo", "montana": "mt", "nebraska": "ne", "nevada": "nv", "new hampshire": "nh",
    "new jersey": "nj", "new mexico": "nm", "new york": "ny", "north carolina": "nc", "north dakota": "nd",
    "ohio": "oh", "oklahoma": "ok", "oregon": "or", "pennsylvania": "pa", "rhode island": "ri",
    "south carolina": "sc", "south dakota": "sd", "tennessee": "tn", "texas": "tx", "utah": "ut", "vermont": "vt",
    "virginia": "va", "washington": "wa", "west virginia": "wv", "wisconsin": "wi", "wyoming": "wy",
    "district of columbia": "dc",
}
US_COUNTRY = {"united states", "united states of america", "usa", "us", "u.s.", "u.s.a."}


def location_norm(loc: str) -> str:
    """'US, CA, Santa Clara' / 'Santa Clara, CA' → 'santa clara, ca'; 'California, United States' → 'ca, us';
    'United States' → 'us'; anything with 'remote' → 'remote' or 'remote-us'."""
    s = re.sub(r"\s+", " ", loc.strip().lower())
    parts = [p.strip().replace(".", "") for p in re.split(r"[,;]", s) if p.strip()]  # "D.C." → "dc", "U.S." → "us"
    if "remote" in s:
        return "remote-us" if any(p in US_COUNTRY for p in parts) or "united states" in s or " us" in s else "remote"
    if len(parts) == 3 and parts[0] in US_COUNTRY and len(parts[1]) == 2:  # Workday "US, CA, Santa Clara"
        return f"{parts[2]}, {parts[1]}"
    out = []
    for i, p in enumerate(parts):
        nxt = parts[i + 1] if i + 1 < len(parts) else None
        is_city = nxt is not None and len(nxt) == 2 and nxt not in US_COUNTRY  # "New York, NY": a city
        out.append("us" if p in US_COUNTRY else p if is_city else US_STATES.get(p, p))
    return ", ".join(out)


BOILERPLATE = re.compile(
    r"equal (?:employment )?opportunity|without regard to|reasonable accommodation|e-verify|affirmative action|"
    r"protected (?:veteran|status|characteristic)|pay transparency|#li-\w+|background check|drug[- ]free|"
    r"privacy (?:policy|notice)|applicants? (?:with|who) (?:disabilities|require)|know your rights|"
    r"accommodations? (?:is|are) available|we are an equal",
    re.I,
)
URL = re.compile(r"https?://\S+")


def clean_description(text: str) -> str:
    """Description for comparison: boilerplate lines (EEO, accommodations, pay-transparency …) and URLs
    removed, whitespace and case normalized. The shown description is untouched."""
    kept = [line for line in text.splitlines() if line.strip() and not BOILERPLATE.search(line)]
    s = URL.sub(" ", "\n".join(kept).lower())
    return re.sub(r"\s+", " ", s).strip()


# ---- stage ----------------------------------------------------------------------------------------

@dataclass
class NormalizeStats:
    normalized: int = 0
    skipped: int = 0
    not_parsed: int = 0
    with_key: int = 0
    company_via: Counter = field(default_factory=Counter)


def normalize_posting(parsed: dict, source: str, aliases: CompanyAliases, source_company: str | None) -> dict:
    url = parsed.get("url") if source_company else None
    ekey = employer_job_key(url) or employer_job_key(parsed.get("apply_url"))
    company, via = aliases.resolve(parsed.get("company"), ekey, source_company)
    desc = clean_description(parsed.get("description_text") or "")
    sh = shingles(desc)
    return {
        "version": NORMALIZER_VERSION,
        "normalized_at": utcnow().isoformat(),
        "company_norm": company,
        "company_via": via,
        "title_norm": title_norm(parsed.get("title")),
        "employer_key": ekey.key if ekey else None,
        "employer_key_from": ("url" if employer_job_key(url) else "apply_url") if ekey else None,
        "req_id": (parsed.get("req_id") or "").strip().upper() or None,
        "location_norm": sorted({location_norm(x) for x in parsed.get("locations") or []}),
        "workplace_type": parsed.get("workplace_type"),
        "employment_type": parsed.get("employment_type"),
        "posted_at": parsed.get("posted_at"),
        "desc_chars": len(desc),
        "desc_hash": hashlib.sha1(desc.encode()).hexdigest() if desc else None,
        "shingle_count": len(sh),
        "minhash": minhash(sh),
    }


def normalize_postings(
    store, registry, sources: list[str] | None = None, again: bool = False
) -> dict[str, NormalizeStats]:
    learned = store.load_aliases()
    companies = registry.companies()
    aliases = CompanyAliases(companies, learned)
    stats: dict[str, NormalizeStats] = defaultdict(NormalizeStats)
    names = sources or store.sources()
    # company sources first, so learning from their boards doesn't depend on order
    for source in sorted(names, key=lambda s: s not in companies):
        st = stats[source]
        source_company = source if source in companies else None
        for ref in store.iter_postings(source):
            posting = store.get_posting(ref)
            if posting.get("state") == "archived":
                continue  # an archived job's posting: kept as an identity only (§9.2)
            parsed = store.get_parsed(ref) if posting.get("state") in ("parsed", "normalized", "linked") else None
            if parsed is None:
                st.not_parsed += 1
                continue
            prev = store.get_normalized(ref)
            if not again and posting.get("state") in ("normalized", "linked") and prev:
                if prev.get("version") == NORMALIZER_VERSION and prev["normalized_at"] >= posting.get("parsed_at", ""):
                    st.skipped += 1
                    _count(st, prev)
                    continue
            norm = normalize_posting(parsed, source, aliases, source_company)
            if posting.get("purged") and prev:  # description text was purged: keep its signature for dedup
                for k in ("desc_chars", "desc_hash", "shingle_count", "minhash"):
                    norm[k] = prev.get(k)
            store.save_normalized(ref, norm)
            st.normalized += 1
            _count(st, norm)
    if aliases.learned != learned:
        store.save_aliases(aliases.learned)
    return dict(stats)


def _count(st: NormalizeStats, norm: dict) -> None:
    st.with_key += bool(norm.get("employer_key"))
    st.company_via[norm.get("company_via")] += 1


def format_normalize(stats: dict[str, NormalizeStats]) -> str:
    header = f"{'source':<10} {'done':>5} {'new':>5} {'employer key':>13}  company resolved via"
    out = [header, "-" * len(header)]
    for source, st in stats.items():
        total = st.normalized + st.skipped
        pct = f"{100 * st.with_key // total}%" if total else "-"
        via = ", ".join(f"{k} {v}" for k, v in st.company_via.most_common())
        out.append(f"{source:<10} {total:>5} {st.normalized:>5} {pct:>13}  {via}"
                   + (f"   ({st.not_parsed} not parsed yet)" if st.not_parsed else ""))
    out.append("")
    out.append("company via: config = company source · board = its employer link points at a configured company ·")
    out.append("             alias/learned = name matched · name = normalized name only")
    out.append("")
    out.append("normalize prepares each posting's keys on its own; to see duplicates, run `auto-app dedup --preview`")
    return "\n".join(out)
