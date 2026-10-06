"""Your profile and answer bank (docs/step3-apply.md §2): `profile/profile.yaml`, `profile/answers.yaml`, your CV.

Filled in once, up front; appliers read them, and a question with no answer here goes to the needs-input queue
(A2). Both files are yours — the app only adds to answers.yaml when you choose "save" in `apply inbox`.
"""

from dataclasses import dataclass, field
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

# answers most applications ask for; `apply profile` lists the ones missing from answers.yaml
COMMON_ANSWERS = {
    "work_authorization": "Are you legally authorized to work in the country of the job?",
    "requires_sponsorship": "Will you now or in the future require visa sponsorship?",
    "earliest_start": "When can you start?",
    "relocation": "Are you willing to relocate?",
    "salary_expectation": "What are your salary expectations?",
}
SENSITIVE = {"work_authorization", "requires_sponsorship", "citizenship", "eeo_gender", "eeo_race", "eeo_veteran",
             "eeo_disability", "criminal_history"}  # only ever answered from answers.yaml or by you (§4)


class Name(BaseModel):
    model_config = ConfigDict(extra="allow")
    first: str
    last: str
    preferred: str | None = None


class Accounts(BaseModel):
    """Site accounts the browser agent signs up / in with (docs/step3-agent.md §6). The password: here (profile/ is
    git-ignored; never sent to Claude) or in the Keychain (`auto-app apply account set`)."""
    email: str | None = None  # one email for every site (default: your email above)
    password: str | None = None  # for every site — signing up where you have no account
    passwords: dict[str, str] = Field(default_factory=dict)  # per site (its host): sign in there with this one
    accept_terms: bool = False  # may the agent tick a site's terms / privacy box when creating an account?


class Profile(BaseModel):
    model_config = ConfigDict(extra="allow")  # education, experience, location, … are free-form for now
    name: Name
    email: str
    phone: str | None = None
    links: dict[str, str] = Field(default_factory=dict)
    cv: str = "profile/cv.pdf"
    accounts: Accounts = Field(default_factory=Accounts)  # site accounts for the browser agent


class Answer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str
    answer: str | bool | int | float
    kind: str = "fact"  # fact | free_text
    adapt: bool = False  # free text: Claude may tailor it lightly to the job


@dataclass
class Me:
    profile: Profile | None
    answers: dict[str, Answer]
    cv: Path | None
    problems: list[str] = field(default_factory=list)  # can't apply until fixed
    missing: list[str] = field(default_factory=list)  # common answers not in answers.yaml (asked when needed)


def _yaml(path: Path, problems: list[str]) -> dict | None:
    if not path.is_file():
        problems.append(f"{path.name} missing — copy profile.example/{path.name} to {path} and fill it in")
        return None
    try:
        data = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as e:
        problems.append(f"{path.name}: not valid YAML ({e})")
        return None
    if not isinstance(data, dict):
        problems.append(f"{path.name}: expected a mapping at the top level")
        return None
    return data


def _err(e: ValidationError) -> str:
    return "; ".join(f"{'.'.join(str(x) for x in err['loc'])}: {err['msg']}" for err in e.errors())


def load_me(home: Path, folder: Path | None = None) -> Me:
    folder = folder or home / "profile"
    problems: list[str] = []
    profile = None
    data = _yaml(folder / "profile.yaml", problems)
    if data is not None:
        try:
            profile = Profile.model_validate(data)
        except ValidationError as e:
            problems.append(f"profile.yaml: {_err(e)}")
    answers: dict[str, Answer] = {}
    raw = _yaml(folder / "answers.yaml", problems)
    for key, value in (raw or {}).items():
        try:
            answers[str(key)] = Answer.model_validate(value)
        except ValidationError as e:
            problems.append(f"answers.yaml: {key}: {_err(e)}")
    cv = None
    if profile:
        cv = Path(profile.cv) if Path(profile.cv).is_absolute() else home / profile.cv
        if not cv.is_file():
            problems.append(f"CV not found: {cv} (profile.yaml `cv`)")
            cv = None
    missing = [k for k in COMMON_ANSWERS if k not in answers] if raw is not None else []
    return Me(profile, answers, cv, problems, missing)


def format_me(me: Me) -> str:
    out = []
    if me.profile:
        n = me.profile.name
        out.append(f"profile   {n.first} {n.last} · {me.profile.email}" + (f" · {me.profile.phone}" if me.profile.phone else ""))
        out.append(f"cv        {me.cv or '— (not found)'}")
    out.append(f"answers   {len(me.answers)} saved"
               + (f" ({sum(a.kind == 'free_text' for a in me.answers.values())} free text)" if me.answers else ""))
    for p in me.problems:
        out.append(f"  ✗ {p}")
    if me.missing:
        out.append("  missing common answers (you'll be asked when an application needs them):")
        out += [f"    {k:<22} {COMMON_ANSWERS[k]}" for k in me.missing]
    if not me.problems:
        out.append("ready to apply" + (" — consider adding the missing answers" if me.missing else ""))
    return "\n".join(out)


def answer_key(label: str, existing) -> str:
    """A key for a new answers.yaml entry, from the question: "Will you require sponsorship?" → will_you_require_sponsorship."""
    import re

    base = "_".join(re.findall(r"[a-z0-9]+", label.lower())[:6]) or "answer"
    key, n = base, 2
    while key in existing:
        key, n = f"{base}_{n}", n + 1
    return key


def save_answer(path: Path, key: str | None, question: str, answer, kind: str = "fact") -> str:
    """Add an entry to answers.yaml (appended as text, so your comments and order stay). Returns its key."""
    current = (yaml.safe_load(path.read_text()) or {}) if path.is_file() else {}
    key = key if key and key not in current else answer_key(question, current)
    entry = {"question": question, "answer": answer} | ({"kind": "free_text"} if kind == "free_text" else {})
    block = yaml.safe_dump({key: entry}, allow_unicode=True, sort_keys=False, width=120)
    text = path.read_text() if path.is_file() else ""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text + ("" if text.endswith("\n") or not text else "\n") + "# saved from apply inbox\n" + block)
    return key
