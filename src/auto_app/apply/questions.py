"""Answering application questions (docs/step3-apply.md §4).

Each question gets a kind, an answer, a source and a confidence:

  standard   name, email, phone, CV, links, location …   → profile.yaml (exact)
  known      sponsorship, start date, relocation …       → answers.yaml: exact label match, else Claude picks an
                                                            entry by meaning (with a confidence)
  sensitive  work authorization, sponsorship, citizenship, demographics, legal agreements
                                                         → ONLY an answers.yaml entry (exact, or picked by Claude at or
                                                            above the threshold) — never drafted, never guessed
  free_text  textareas ("Why us?")                        → a saved free-text answer ({company} / {title} filled in;
                                                            `adapt: true` → Claude tailors it, shown as a draft), else
                                                            drafted by Claude — a draft is shown to you before submitting
  choice     dropdowns: the answer mapped onto the listed options (exact, yes/no, or Claude's pick)

What can't be answered: required → needs input (asked in `apply inbox`); optional → left blank.
"""

import re
from dataclasses import asdict, dataclass, field

from auto_app.apply.profile import SENSITIVE, Answer, Me

SENSITIVE_WORDS = re.compile(
    r"authori[sz]|sponsor|visa|citizen|green card|permanent resident|gender|sex\b|race|ethnic|hispanic|latin|"
    r"veteran|disabilit|criminal|convict|felony|arbitrat|agree|consent|certif|attest|acknowledg|terms|privacy|"
    r"pronoun|sexual orientation|transgender|age\b|date of birth|social security", re.I)
EEOC_FIELDS = {"gender", "race", "veteran_status", "disability_status", "hispanic_ethnicity"}


@dataclass
class Question:
    id: str  # the form's field name (Greenhouse: first_name, question_8581812008, gender …)
    label: str
    type: str  # text | textarea | file | select | multiselect
    required: bool = False
    options: list[dict] = field(default_factory=list)  # [{"label": "Yes", "value": 1}, …]
    kind: str = "known"  # standard | known | sensitive | free_text
    answer: str | list | None = None  # the text, or the chosen option label(s); a file path for file fields
    source: str | None = None  # profile | bank:<key> | you | draft | skipped
    confidence: float | None = None
    seen: bool = True  # drafts start unseen; shown in review → seen
    note: str = ""  # why it's unanswered, or what Claude said

    @property
    def open(self) -> bool:
        return self.answer in (None, "", []) and self.required and self.source != "skipped"

    def to_row(self) -> dict:
        return asdict(self)


def norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def classify(q: Question) -> str:
    if q.id in EEOC_FIELDS or SENSITIVE_WORDS.search(q.label or ""):
        return "sensitive"
    if q.id in STANDARD or norm(q.label) in STANDARD_LABELS:
        return "standard"
    if q.type == "textarea":
        return "free_text"
    return "known"


# ---- standard fields from profile.yaml --------------------------------------------------------------

STANDARD = {"first_name", "last_name", "preferred_name", "email", "phone", "resume", "cover_letter", "country",
            "location", "resume_text", "cover_letter_text"}
STANDARD_LABELS = {"linkedin profile": "links.linkedin", "linkedin": "links.linkedin", "linkedin url": "links.linkedin",
                   "github": "links.github", "github profile": "links.github", "website": "links.portfolio",
                   "portfolio": "links.portfolio", "personal website": "links.portfolio",
                   "location city": "location.city", "city": "location.city", "current location": "location.city",
                   "country": "location.country", "state": "location.state", "state province": "location.state",
                   "province": "location.state", "postal code": "location.postal_code", "zip code": "location.postal_code",
                   "zip": "location.postal_code", "address": "location.address1", "address line 1": "location.address1",
                   "street address": "location.address1", "address line 2": "location.address2",
                   "current company": "experience.0.company", "current employer": "experience.0.company",
                   "current title": "experience.0.title", "school": "education.0.school", "degree": "education.0.degree"}

# a field known only by its label (the agent, forms without Greenhouse's field names) → the standard field it is
LABEL_IDS = {"first name": "first_name", "given name": "first_name", "legal first name": "first_name",
             "last name": "last_name", "family name": "last_name", "surname": "last_name", "legal last name": "last_name",
             "preferred name": "preferred_name", "preferred first name": "preferred_name",
             "email": "email", "email address": "email", "e mail": "email",
             "phone": "phone", "phone number": "phone", "mobile": "phone", "mobile phone": "phone",
             "mobile number": "phone", "telephone": "phone", "resume": "resume", "cv": "resume", "resume cv": "resume"}


def standard_id(label: str) -> str | None:
    return LABEL_IDS.get(norm(label))


def _get(data, path: str):
    for part in path.split("."):
        if isinstance(data, list):
            data = data[int(part)] if part.isdigit() and int(part) < len(data) else None
        elif isinstance(data, dict):
            data = data.get(part)
        else:
            return None
    return data


def standard_answer(q: Question, me: Me) -> str | None:
    p = me.profile.model_dump() if me.profile else {}
    name = p.get("name") or {}
    direct = {"first_name": name.get("first"), "last_name": name.get("last"), "preferred_name": name.get("preferred"),
              "email": p.get("email"), "phone": p.get("phone"),
              "resume": str(me.cv) if me.cv else None, "country": _get(p, "location.country"),
              "location": ", ".join(x for x in (_get(p, "location.city"), _get(p, "location.state")) if x) or None}
    if q.id in direct:
        return direct[q.id]
    if q.id in ("resume_text", "cover_letter", "cover_letter_text"):
        return None  # optional alternatives to the CV upload: left blank
    path = STANDARD_LABELS.get(norm(q.label))
    value = _get(p, path) if path else None
    return str(value) if value not in (None, "") else None


# ---- answers.yaml ---------------------------------------------------------------------------------------

def fill_placeholders(text: str, job: dict) -> str:
    return (text.replace("{company}", job.get("company") or "the company")
                .replace("{title}", job.get("title") or "the role"))


def exact_entry(q: Question, me: Me, job: dict) -> tuple[str, Answer] | None:
    """An answers.yaml entry whose question is the same as this one (after placeholders, ignoring case and punctuation)."""
    label = norm(q.label)
    for key, a in me.answers.items():
        if norm(fill_placeholders(a.question, job)) == label or norm(a.question) == label:
            return key, a
    return None


def as_text(value) -> str:
    if isinstance(value, bool):
        return "Yes" if value else "No"
    return str(value)


def to_option(answer, options: list[dict]) -> str | None:
    """Map an answer onto one of the listed options by label: exact, case-insensitive, or yes / no."""
    text = as_text(answer)
    labels = [str(o.get("label", "")) for o in options]
    for lab in labels:
        if lab == text:
            return lab
    for lab in labels:
        if norm(lab) == norm(text):
            return lab
    yn = {"yes": "yes", "true": "yes", "no": "no", "false": "no"}.get(norm(text))
    if yn:
        hits = [lab for lab in labels if norm(lab).split()[:1] == [yn]]
        if len(hits) == 1:
            return hits[0]
    return None


def is_sensitive_key(key: str) -> bool:
    return key in SENSITIVE or key.startswith("eeo")


# ---- resolving every question --------------------------------------------------------------------------------

@dataclass
class Resolution:
    questions: list[Question]

    @property
    def open(self) -> list[Question]:
        return [q for q in self.questions if q.open]

    @property
    def drafts_unseen(self) -> list[Question]:
        return [q for q in self.questions if q.source == "draft" and not q.seen]


def resolve(questions: list[Question], me: Me, job: dict, matcher=None, threshold: float = 0.9) -> Resolution:
    """Answer what can be answered: profile, exact answers.yaml matches, then Claude (matching by meaning, options,
    drafts) for the rest. `matcher(questions, me, job) -> {id: {"key", "answer", "option", "draft", "confidence", "note"}}`."""
    pending: list[Question] = []
    for q in questions:
        q.kind = classify(q)
        if q.type == "file" and q.id != "resume":
            q.source = "skipped"
            continue
        if q.kind == "standard":
            value = standard_answer(q, me)
            if value is not None:
                _set(q, value, "profile", 1.0)
            elif not q.required:
                q.source = "skipped"
            continue
        hit = exact_entry(q, me, job)
        if hit and _apply_entry(q, hit[0], hit[1], job, 1.0):
            continue
        pending.append(q)
    if pending and matcher is not None:
        try:
            picks = matcher(pending, me, job) or {}
        except Exception as e:  # Claude unavailable: everything pending goes to you
            picks = {}
            for q in pending:
                q.note = f"couldn't ask Claude: {type(e).__name__}: {e}"[:200]
        for q in pending:
            p = picks.get(q.id) or {}
            conf = float(p.get("confidence") or 0)
            key = p.get("key")
            if key and key in me.answers and conf >= threshold:
                if _apply_entry(q, key, me.answers[key], job, conf, option=p.get("option")):
                    continue
            if q.kind == "sensitive":
                q.note = q.note or "sensitive: answered only from answers.yaml or by you"
                continue
            if q.kind == "free_text" and p.get("draft"):
                _set(q, p["draft"], "draft", conf or None, seen=False)
                continue
            if q.type in ("select", "multiselect") and p.get("option") and conf >= threshold:
                opt = to_option(p["option"], q.options)
                if opt:
                    _set(q, opt, "claude", conf)
                    continue
            if not q.note:
                q.note = p.get("note") or ("no confident answer" if p else "no saved answer")
    for q in pending:
        if q.answer in (None, "", []) and not q.required:
            q.source = "skipped"  # optional and unanswered: left blank, never asked
    return Resolution(questions)


def _set(q: Question, value, source: str, confidence: float | None, seen: bool = True) -> None:
    q.answer, q.source, q.confidence, q.seen = value, source, confidence, seen


def _apply_entry(q: Question, key: str, a: Answer, job: dict, conf: float, option: str | None = None) -> bool:
    """Use an answers.yaml entry for this question; False if it doesn't fit (e.g. no matching option)."""
    text = fill_placeholders(as_text(a.answer), job)
    if q.kind == "free_text" or a.kind == "free_text":
        if a.adapt:
            _set(q, text, "draft", conf, seen=False)  # Claude may tailor it at submit time; shown as a draft
        else:
            _set(q, text, f"bank:{key}", conf)
        return True
    if q.type in ("select", "multiselect"):
        opt = to_option(a.answer, q.options) or (to_option(option, q.options) if option else None)
        if not opt:
            q.note = f"answers.yaml `{key}` ({text}) doesn't match the options"
            return False
        _set(q, opt, f"bank:{key}", conf)
        return True
    _set(q, text, f"bank:{key}", conf)
    return True
