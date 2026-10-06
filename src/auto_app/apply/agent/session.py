"""One agent run on one application (docs/step3-agent.md §3–§5): the tools the model calls, each returning text.

Browser tools are generic (snapshot, click, type, select, check, upload_cv, press, scroll, wait, screenshot); the data
tools are ours (answer_for, ask_user, done). The guards (guards.py) are enforced here, in the tools — whatever the model
or a page says. Every call is logged (log.jsonl); every value put on the form is recorded as a Question, so the
review shows what was filled and where it came from.
"""

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from auto_app.apply.agent.guards import ACCOUNT_PAGE, HONEYPOT, allowed_navigation, allowed_sites, click_refusal
from auto_app.apply.agent.snapshot import SNAPSHOT_JS, render
from auto_app.apply.questions import Question, classify, norm, resolve, standard_id

MODES = ("start", "fill", "submit")
REF = re.compile(r"^(?:f(\d+))?e(\d+)$")
TYPES = {"textbox": "text", "textarea": "textarea", "select": "select", "combobox": "select", "listbox": "multiselect",
         "checkbox": "checkbox", "radio": "radio", "file": "file", "switch": "checkbox"}


@dataclass
class Result:
    status: str | None = None  # done(): needs_input | ready | filled | submitted | action_needed | unsupported
    note: str = ""
    more_steps: bool = False


@dataclass
class AgentSession:
    page: object  # a Playwright (async) page
    mode: str  # start | fill | submit
    app: dict  # the application (apply_url, title, company, questions from earlier passes)
    me: object  # profile.Me
    matcher: object = None
    threshold: float = 0.9
    time_limit_s: float = 600
    log_path: Path | None = None
    progress: object = None  # progress(text): live status for the Apply section
    started: float = field(default_factory=time.monotonic)
    items: dict = field(default_factory=dict)  # ref -> the snapshot item (role, name, …)
    filled: dict = field(default_factory=dict)  # question id -> Question (what was put on the form)
    asked: dict = field(default_factory=dict)  # question id -> Question (open: for the user)
    answers: dict = field(default_factory=dict)  # norm(label) -> the answer answer_for gave (sensitive check)
    result: Result = field(default_factory=Result)
    blocked: list = field(default_factory=list)  # navigations the guard stopped
    account: dict = field(default_factory=dict)  # the site account used: {"site", "email", "created"}
    tokens: dict = field(default_factory=dict)
    get_password: object = None  # (email, site) -> the Keychain password (accounts.get_password; tests: a fake)
    has_password: object = None  # (email, site or None) -> bool: a password stored for that site / the default
    find_account: object = None  # (site) -> the account on record, or None

    def __post_init__(self):
        assert self.mode in MODES
        self.allowed = allowed_sites(self.app.get("apply_url") or "")
        self.known = {norm(q.label): q for q in self.app.get("questions") or []
                      if q.answer not in (None, "", []) and q.source not in ("skipped",)}
        self.job = {"title": self.app.get("title"), "company": self.app.get("company"), "description": ""}
        from auto_app.apply import accounts

        acc = self._accounts()
        keychain_get = self.get_password or accounts.get_password
        keychain_has = self.has_password or accounts.has_password
        # profile.yaml first (accounts.passwords for a site, accounts.password for every site), then the Keychain
        self.get_password = lambda email, site: acc.passwords.get(site or "") or acc.password or keychain_get(email, site)
        self.has_password = lambda email, site: (bool(acc.passwords.get(site)) if site else bool(acc.password)) \
            or keychain_has(email, site)

    # ---- plumbing ---------------------------------------------------------------------------------

    @property
    def time_left(self) -> float:
        return self.time_limit_s - (time.monotonic() - self.started)

    def _log(self, tool: str, args: dict, out: str) -> None:
        if self.log_path is None:
            return
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        safe = {k: ("••••" if k == "text" and self._is_secret(args.get("ref")) else v) for k, v in args.items()}
        with self.log_path.open("a") as f:
            f.write(json.dumps({"t": round(time.monotonic() - self.started, 1), "tool": tool, "args": safe,
                                "out": out[:2000]}, ensure_ascii=False) + "\n")

    def _say(self, text: str) -> None:
        if self.progress:
            try:
                self.progress(text)
            except Exception:
                pass

    def _is_secret(self, ref) -> bool:
        it = self.items.get(ref or "") or {}
        return it.get("value") == "••••" or "password" in (it.get("name") or "").lower()

    async def install_guards(self) -> None:
        """The main frame stays on the job's site and the ATS / sign-in sites it uses (§5)."""
        async def route(r):
            req = r.request
            if req.is_navigation_request() and req.frame == self.page.main_frame \
                    and not allowed_navigation(req.url, self.allowed):
                self.blocked.append(req.url)
                await r.abort()
                return
            await r.fallback()  # on to any other route (tests' fake pages), else the network
        await self.page.context.route("**/*", route)

    def _frame(self, ref: str):
        m = REF.match(ref or "")
        if not m:
            raise ValueError(f"unknown ref {ref!r} — take a snapshot and use a ref from it")
        frames = self.page.frames
        i = int(m.group(1) or 0)
        if i >= len(frames):
            raise ValueError(f"frame of {ref!r} is gone — take a new snapshot")
        return frames[i]

    async def _el(self, ref: str):
        el = self._frame(ref).locator(f'[data-aa-ref="{ref}"]')
        if not await el.count():
            raise ValueError(f"{ref!r} isn't on the page any more — take a new snapshot")
        return el.first

    def _time_up(self) -> str | None:
        if self.time_left <= 0:
            return "time is up: call done now with what you have"
        return None

    def _label(self, ref: str) -> str:
        """The question a field asks: a radio's / grouped checkbox's group, else its own name."""
        it = self.items.get(ref) or {}
        return (it.get("group") or it.get("name") or ref).rstrip("* ").strip()

    def _record(self, ref: str, value, source: str = "agent") -> None:
        """What went on the form, for the review: by label (stable across runs)."""
        it = self.items.get(ref) or {}
        label = self._label(ref)
        hit = self.answers.get(norm(label))
        qid = "agent:" + (norm(label) or ref)
        q = Question(id=qid, label=label, type=TYPES.get(it.get("role"), "text"), required=bool(it.get("required")),
                     options=[{"label": o} for o in it.get("options") or []], answer=value,
                     source=hit[1] if hit else source)
        q.kind = classify(q)
        self.filled[qid] = q
        self.asked.pop(qid, None)

    def _guard_value(self, ref: str, value) -> str | None:
        """Sensitive fields take only the answer answer_for gave (§5)."""
        it = self.items.get(ref) or {}
        label = self._label(ref)
        q = Question(id="x", label=label, type=TYPES.get(it.get("role"), "text"))
        if classify(q) != "sensitive":
            return None
        given = self.answers.get(norm(label))
        if given is None:
            return (f"refused: “{label[:80]}” is a sensitive question — call answer_for first; if it has no answer, "
                    "ask_user")
        ok = str(given[0]).strip().lower()
        v = str(value).strip().lower()
        if v == ok or v in ok or ok in v or (ok in ("yes", "true", "checked") and v in ("on", "true", "checked", "yes")) \
                or (ok in ("no", "false") and v in ("off", "false", "no", "unchecked")):
            return None
        return f"refused: “{label[:80]}” is sensitive — only the user's saved answer ({given[0]!r}) may go there"

    # ---- browser tools (generic) -------------------------------------------------------------------

    async def snapshot(self) -> str:
        if (t := self._time_up()):
            return t
        if not self.items:
            self._say("reading the page")
        frames, self.items = [], {}
        for i, frame in enumerate(self.page.frames):
            if frame.url in ("about:blank", "") and i:
                continue
            try:
                data = await frame.evaluate(SNAPSHOT_JS, f"f{i}" if i else "")
            except Exception:
                continue  # a frame that went away or refuses scripts
            if i and not any(it["role"] != "text" for it in data["items"]):
                continue  # trackers, captcha anchors without fields …
            frames.append({"index": i, "url": frame.url, "title": data.get("title"), "items": data["items"]})
            for it in data["items"]:
                if "ref" in it:
                    self.items[it["ref"]] = it
        text = render(frames)
        if self.blocked:
            text += "\n(blocked navigation to: " + ", ".join(self.blocked[-3:]) + " — stay on the application)"
        return text[:30000]

    async def click(self, ref: str) -> str:
        if (t := self._time_up()):
            return t
        it = self.items.get(ref) or {}
        el = await self._el(ref)
        in_filled_form = await el.evaluate(
            "e => { const f = e.closest('form'); return !!f && [...f.querySelectorAll('input:not([type=hidden]), "
            "textarea, select')].filter(i => i.value).length >= 2; }")
        auth = await self._auth_form(el) or bool(ACCOUNT_PAGE.search(await self.page.title()))
        why = click_refusal(self.mode, it.get("name") or "", it.get("role") or "", in_filled_form, auth=auth)
        if why:
            return why
        pages, before, blocked = len(self.page.context.pages), self.page.url, len(self.blocked)
        how = ""
        try:
            await el.click(timeout=5_000)
        except Exception:  # something covers it (an overlay, a cookie banner, a sticky header): click it directly
            await el.evaluate("e => e.click()")
            how = " (something covered it — clicked it directly)"
        await self.page.wait_for_timeout(1200)
        if len(self.blocked) > blocked:  # the guard stopped it: back to where we were
            if self.page.url != before:
                await self.page.goto(before, wait_until="domcontentloaded")
            return f"refused: that leads off the application ({self.blocked[-1][:100]}) — stay on the application"
        if len(self.page.context.pages) > pages:  # it opened a new tab: continue there
            self.page = self.page.context.pages[-1]
            await self.page.wait_for_load_state("domcontentloaded")
            return "clicked — a new tab opened (now working in it); take a snapshot"
        self._say(f"clicked “{(it.get('name') or ref)[:50]}”")
        return f"clicked{how}; take a snapshot to see the result"

    async def _auth_form(self, el) -> bool:
        """Is this element part of a sign-in / create-account form (a password field next to it)?"""
        return await el.evaluate(
            "e => { const f = e.closest('form, [role=dialog], main') || document;"
            " const fields = [...f.querySelectorAll('input:not([type=hidden]):not([type=checkbox])')];"
            " if (f.querySelector('input[type=password]')) return fields.length <= 5;"  # sign in / create account
            # (an application form with a password among many fields isn't one) — or email first, password next:
            " const mail = fields.some(i => i.type === 'email' || /mail|user/i.test(i.name + i.id + i.autocomplete));"
            " return fields.length <= 2 && mail &&"
            "   /sign.?in|log.?in|create (an )?account|sign.?up|register/i.test(document.title + ' ' + f.innerText.slice(0, 2000)); }")

    async def type(self, ref: str, text: str) -> str:
        if (t := self._time_up()):
            return t
        if self._is_secret(ref) or (self.items.get(ref) or {}).get("value") == "••••":
            return "refused: a password field — use fill_secret(ref, \"password\")"
        if HONEYPOT.search((self.items.get(ref) or {}).get("name") or ""):
            return "refused: a field for robots only (a honeypot) — leave it empty"
        if (why := self._guard_value(ref, text)):
            return why
        el = await self._el(ref)
        await el.fill("", timeout=10_000)
        await el.fill(str(text), timeout=10_000)
        self._record(ref, text)
        self._say(f"filling “{(self.items.get(ref, {}).get('name') or ref)[:50]}”")
        return "typed"

    async def select(self, ref: str, option: str) -> str:
        """A native <select>: pick by label. A custom dropdown: click it, then click the option in a new snapshot."""
        if (t := self._time_up()):
            return t
        if (why := self._guard_value(ref, option)):
            return why
        it = self.items.get(ref) or {}
        el = await self._el(ref)
        if it.get("role") not in ("select", "listbox"):
            return "not a native dropdown: click it, take a snapshot, then click the option you want"
        opts = it.get("options") or []
        pick = next((o for o in opts if o == option), None) or next((o for o in opts if norm(o) == norm(option)), None) \
            or next((o for o in opts if norm(option) in norm(o)), None)
        if pick is None:
            return f"no option like {option!r}; the options are: {' | '.join(opts[:40])}"
        await el.select_option(label=pick, timeout=10_000)
        self._record(ref, pick)
        return f"selected “{pick}”"

    async def check(self, ref: str, on: bool = True) -> str:
        if (t := self._time_up()):
            return t
        it = self.items.get(ref) or {}
        value = it.get("name") if it.get("group") and it.get("role") == "radio" else "yes"  # a radio: its option
        el = await self._el(ref)
        terms_ok = self._accounts().accept_terms and await self._auth_form(el)  # you allowed it, on account forms
        if on and not terms_ok and (why := self._guard_value(ref, value)):
            return why
        try:
            await (el.check if on else el.uncheck)(timeout=10_000)
        except Exception:
            await el.click(timeout=10_000)  # a custom checkbox / radio
        self._record(ref, value if on else "no")
        return "checked" if on else "unchecked"

    async def upload_cv(self, ref: str) -> str:
        if (t := self._time_up()):
            return t
        if not self.me.cv or not Path(self.me.cv).is_file():
            return "no CV on file (profile/cv.pdf) — ask_user about it"
        el = await self._el(ref)
        try:
            await el.set_input_files(str(self.me.cv), timeout=10_000)
        except Exception:  # a button that opens the file chooser
            async with self.page.expect_file_chooser(timeout=10_000) as fc:
                await el.click()
            await (await fc.value).set_files(str(self.me.cv))
        await self.page.wait_for_timeout(1500)
        self._say("attaching your CV")
        self._record(ref, Path(self.me.cv).name, "profile")
        return "CV attached (the site may fill some fields from it: take a snapshot)"

    def _accounts(self):
        from auto_app.apply.profile import Accounts

        p = self.me.profile
        return getattr(p, "accounts", None) or Accounts()

    async def account_info(self) -> str:
        """For the site now open: sign in, or create an account — or what you still have to provide (§6)."""
        from auto_app.apply.accounts import account_email, account_site, plan

        email = account_email(self.me)
        site = account_site(self.page.url)
        known = self.find_account(site) if self.find_account else None
        site_pw = bool(email) and self.has_password(email, site)
        default_pw = bool(email) and self.has_password(email, None)
        self._say(f"checking the account for {site}")
        return plan(email, site, known, site_pw, default_pw)

    async def fill_secret(self, ref: str, what: str) -> str:
        """Type the account email or password you provided (§6) — the model never sees the password."""
        from auto_app.apply.accounts import account_email, account_site, get_password

        if (t := self._time_up()):
            return t
        email = account_email(self.me)
        if not email:
            return "no account email — done(\"action_needed\", \"set accounts.email in profile.yaml\")"
        site = account_site(self.page.url)
        el = await self._el(ref)
        if what == "password":
            if not await el.evaluate("e => e.type === 'password'"):
                return "refused: not a password field"
            secret = self.get_password(email, site)
            if not secret:
                return ("no password set for site accounts — done(\"action_needed\", \"set an account password: "
                        "auto-app apply account set\")")
            await el.fill(secret, timeout=10_000)
            creating = await el.evaluate(  # a new password + its repeat, both on screen (hidden ones don't count:
                "e => { const f = e.closest('form, [role=dialog], main') || document; "  # Workday hides sign-up's)
                "return [...f.querySelectorAll('input[type=password]')]"
                ".filter(i => i.offsetParent !== null && i.getClientRects().length).length >= 2; }")
            self.account = {"site": site, "email": email,
                            "created": bool(self.account.get("created")) or bool(creating)}
            self._say("creating an account" if creating else "signing in")
            return "password typed (not shown)"
        if what == "email":
            await el.fill(email, timeout=10_000)
            self.account.setdefault("site", site)
            self.account.setdefault("email", email)
            return f"typed the account email ({email})"
        return "what must be \"email\" or \"password\""

    async def press(self, key: str) -> str:
        await self.page.keyboard.press(key)
        await self.page.wait_for_timeout(400)
        return f"pressed {key}"

    async def scroll(self, direction: str = "down") -> str:
        await self.page.mouse.wheel(0, 700 if direction != "up" else -700)
        await self.page.wait_for_timeout(400)
        return "scrolled"

    async def wait(self, seconds: float = 2) -> str:
        await self.page.wait_for_timeout(min(float(seconds), 15) * 1000)
        return "waited"

    async def screenshot(self) -> bytes:
        return await self.page.screenshot(type="png")

    # ---- data tools (ours) ------------------------------------------------------------------------

    async def answer_for(self, fields: list[dict]) -> str:
        """[{label, type?, options?, required?}] → an answer and its source for each, from earlier answers, profile,
        answers.yaml and Claude (drafts for free text) — or "ask the user"."""
        self._say(f"looking up answers for {len(fields)} field(s)")
        qs, out = [], []
        for i, f in enumerate(fields):
            label = (f.get("label") or "").rstrip("* ").strip()
            k = self.known.get(norm(label))
            if k is not None:  # answered on an earlier pass (by you, or from your files)
                self.answers[norm(label)] = (k.answer, k.source or "you")
                out.append(f"{label}: {k.answer!r} (from {k.source or 'you'})")
                continue
            q = Question(id=standard_id(label) or f"q{i}", label=label, type=f.get("type") or "text",
                         required=bool(f.get("required", True)),
                         options=[{"label": str(o)} for o in f.get("options") or []])
            qs.append(q)
        if qs:
            resolve(qs, self.me, self.job, self.matcher, self.threshold)
        for q in qs:
            if q.type == "file":
                out.append(f"{q.label}: use upload_cv for a CV / resume field; other files: leave empty")
            elif q.answer not in (None, "", []):
                self.answers[norm(q.label)] = (q.answer, q.source or "profile")
                tag = " — a draft, the user reviews it" if q.source == "draft" else ""
                out.append(f"{q.label}: {q.answer!r} (from {q.source}){tag}")
            elif q.source == "skipped":
                out.append(f"{q.label}: optional, no answer — leave it empty")
            else:
                why = q.note or ("sensitive: only the user's saved answers" if q.kind == "sensitive" else "no answer")
                out.append(f"{q.label}: no answer ({why}) — ask_user if it's required")
        return "\n".join(out) or "nothing asked"

    async def ask_user(self, label: str, question: str = "", options: list | None = None, ref: str | None = None) -> str:
        it = self.items.get(ref or "") or {}
        label = (label or it.get("name") or "").rstrip("* ").strip()
        qid = "agent:" + norm(label)
        if qid in self.filled:
            return "already filled — no need to ask"
        q = Question(id=qid, label=label, type=TYPES.get(it.get("role"), "select" if options else "text"),
                     required=True, options=[{"label": str(o)} for o in options or it.get("options") or []],
                     note=question[:300] if question and question != label else "")
        q.kind = classify(q)
        self.asked[qid] = q
        self._say(f"asking you “{label[:50]}”")
        return "recorded — the user will answer it; leave the field empty and go on"

    async def done(self, status: str, note: str = "", more_steps: bool = False) -> str:
        self.result = Result(status=status, note=note[:300], more_steps=bool(more_steps))
        return "finished"

    # ---- the outcome ----------------------------------------------------------------------------------

    async def unfilled_required(self) -> list[Question]:
        """A last check (start mode): required fields still empty on the step, not asked — asked now."""
        await self.snapshot()
        missed, groups = [], {}
        for ref, it in self.items.items():  # a radio group is answered when any of its options is checked
            if it.get("group"):
                groups[it["group"]] = groups.get(it["group"], False) or bool(it.get("checked"))
        seen_groups = set()
        for ref, it in self.items.items():
            if not it.get("required") or it.get("role") in ("button", "link", "option", "tab"):
                continue
            if it.get("group"):
                if it["group"] in seen_groups:
                    continue
                seen_groups.add(it["group"])
                empty = not groups[it["group"]]
            else:
                empty = (it.get("checked") is False) if "checked" in it else not it.get("value")
            label = self._label(ref)
            qid = "agent:" + norm(label)
            if empty and qid not in self.asked and label:
                q = Question(id=qid, label=label, type=TYPES.get(it.get("role"), "text"), required=True,
                             options=[{"label": o} for o in it.get("options") or []],
                             note="required and still empty on the form")
                q.kind = classify(q)
                if qid in self.filled:
                    q.note = f"“{self.filled[qid].answer}” didn't take on the form"
                    self.filled.pop(qid)
                missed.append(q)
                self.asked[qid] = q
        return missed

    def questions(self) -> list[Question]:
        return list(self.filled.values()) + list(self.asked.values())
