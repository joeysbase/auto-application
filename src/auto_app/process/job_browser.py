"""`auto-app job browse` — the job pool in a full-screen app (Textual), and where you review Step 2's results
(docs/step2-select.md §8).

Left: the jobs in one tab (All, Not evaluated, Recommended, Un-recommended, Want to apply, Applied, Not applied,
Archived; `tab` switches). Right: the selected job — what `job show` prints, every filter's verdict, reason
and lookups with your label, then the description. Keys save at once: `a` want to apply, `n` not apply,
`g` / `b` the picked filter should / shouldn't recommend this job (`B`: no filter should), `m` any state, `u` undo;
`s` / `c` / `/` narrow the list. Audit mode (`filter audit`, §7.4) shows only the Audit tab — random samples of
each filter's "no" jobs — and only the keys involved in labelling them (no decisions, states or narrowing); `d` draws
more; `e` shows the evaluation panel, recomputed after every label. Your answers go through `Review`
(select/review.py); archived jobs can be labelled (they're training data) but not decided on or changed.
"""

import webbrowser

from rich.markup import escape
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.screen import ModalScreen, Screen
from textual.widgets import DataTable, Input, OptionList, Static
from textual.widgets.option_list import Option

from auto_app.core.config import UiConfig
from auto_app.process.jobs_view import STATE_LABEL, _cut, _date, expiry, format_job
from auto_app.select import audit
from auto_app.select.evaluate import evaluate
from auto_app.select.parse import verdict_text
from auto_app.select.review import Review, Undo
from auto_app.ui import KEY_BAR_CSS, key_bar

# "recommended": new — recommended and not decided yet (a / n move it out); "all_recommended": every job a filter
# recommended, whatever you decided. What you queued (want to apply) is in Apply (F3) → Queued
TABS = ("all", "not_evaluated", "recommended", "all_recommended", "un_recommended", "applied", "not_applied")
AUDIT_TABS = ("audit",)  # `filter audit`: only the randomly drawn jobs — the pool tabs would invite non-random labels
TAB_TITLES = {"all": "All", "not_evaluated": "Not evaluated", "recommended": "New recommended",
              "all_recommended": "All recommended",
              "un_recommended": "Un-recommended", "want_to_apply": "Want to apply", "applied": "Applied",
              "not_applied": "Not applied", "archived": "Archived", "audit": "Audit"}
AUDIT_MARK = {None: "?", True: "✗", False: "✓"}  # not labelled yet / good for some filter (a miss) / bad for all
MARKABLE = ("want_to_apply", "applied", "not_applied", "recommended", "un_recommended", "not_evaluated")
DECISION_MARK = {"want_to_apply": "★", "not_applied": "✗", "applied": "✓"}
FOLLOW_EVERY_S = 3  # how often theme auto checks the system's mode
AUDIT_ONLY = {"draw"}  # keys that only make sense in `filter audit`
BROWSE_ONLY = {"decide", "mark", "narrow", "next_list", "prev_list"}  # decisions, states, narrowing, tabs: not audit


class JobBrowser(Review):
    """The data side: jobs per tab, narrowed by source / company / filter / search; `mark` with undo; and
    everything `Review` does (details, decisions, feedback)."""

    def __init__(self, store, filters: list, ttl=None, sources: list[str] | None = None, company: str | None = None,
                 only_filters: list[str] | None = None, noise_limit: float = 0.05, audit_more: int = 10):
        super().__init__(store, filters, ttl, only_filters)
        self.sources = list(sources or [])
        self.company = company or ""
        self.noise_limit = noise_limit
        self.audit_more = audit_more

    def _narrow(self, rows: list[dict]) -> list[dict]:
        if self.sources:
            rows = [j for j in rows if set(self.sources) & set(j.get("sources") or [])]
        if self.company:
            c = self.company.lower()
            rows = [j for j in rows if c in (j.get("company") or j.get("company_norm") or "").lower()]
        if self.only:
            judged = {r["job_id"] for r in self.store.judgment_rows() if r["filter"] in self.only}
            rows = [j for j in rows if j["job_id"] in judged]
        return rows

    def jobs(self, kind: str, search: str = "", keep=()) -> list[dict]:
        """Newest first. keep: jobs changed in this tab during this session stay in it (with their new state and
        mark), so you can see what you did and change your mind."""
        if kind == "audit":
            status = audit.audit_status(self.store, self.only or None)
            rows = [{**j, "audit": status[j["job_id"]]} for j in self.store.jobs(archived=None) if j["job_id"] in status]
        elif kind == "archived":
            rows = self.store.jobs(archived=True)
        else:
            rows = self.store.jobs()
            if kind == "all_recommended":
                rows = [j for j in rows if j.get("step2") == "recommended" or j["job_id"] in keep]
            elif kind != "all":
                rows = [j for j in rows if j["state"] == kind or j["job_id"] in keep]
        rows = self._narrow(rows)
        if search:
            q = search.lower()
            rows = [j for j in rows if q in f"{j.get('title') or ''} {j.get('company') or ''} "
                                             f"{' '.join(j.get('sources') or [])} {j['job_id']}".lower()]
        if kind == "audit":  # what's left to label first, then the newest
            return sorted(rows, key=lambda j: (all(v is not None for v in j["audit"].values()), -j["job_id"]))
        return sorted(rows, key=lambda j: (j.get("posted_at") or j.get("first_seen_at") or "", j["job_id"]), reverse=True)

    def counts(self) -> dict[str, int]:
        active = self._narrow(self.store.jobs())
        out = {k: sum(1 for j in active if j["state"] == k) for k in (*TABS, *MARKABLE)}  # every state, not only the tabs
        out["all_recommended"] = sum(1 for j in active if j.get("step2") == "recommended")
        out["all"] = len(active)
        out["archived"] = len(self._narrow(self.store.jobs(archived=True)))
        status = audit.audit_status(self.store, self.only or None)
        out["audit"] = sum(1 for v in status.values() if None in v.values())  # jobs still to label
        out["audit_total"] = len(status)
        return out

    # ---- audit and evaluation (§7.4, §7.6) ----------------------------------------------------------

    def audit_filters(self) -> list[str]:
        """The filters in scope: -f ones, else every filter that has judged a job."""
        return sorted(self.only) if self.only else sorted({r["filter"] for r in self.store.judgment_rows()})

    def draw(self, n: int | None = None) -> list[str]:
        """Draw n more random "no" jobs per filter in scope; returns one line per filter."""
        out = []
        for name in self.audit_filters():
            flt = self.filters.get(name)
            d = audit.draw(self.store, name, n or self.audit_more, flt.version if flt else None)
            out.append(f"{name}: drew {len(d.jobs)} of {d.pool_size}" if d else f"{name}: nothing left to draw")
        return out

    def evaluation(self):
        return evaluate(self.store, self.audit_filters(), self.noise_limit)

    def label_all(self, job_id: int, good: bool) -> list[str]:
        """Label the job good / bad for every filter shown on it (one undo)."""
        d = self.detail(job_id)
        prev = self.store.feedback_of(job_id)
        for v in d.verdicts:
            self._label(job_id, v, good, None, "explicit", d.text_for_labels)
        self.undo_stack.append(Undo("feedback_many", job_id, {"prev": {v.filter: prev.get(v.filter) for v in d.verdicts}}))
        return [v.filter for v in d.verdicts]

    def mark(self, job_id: int, to: str) -> None:
        if to not in MARKABLE:
            raise ValueError(to)
        step2, decision = self.store.mark_job(job_id, to)
        if self.ttl is not None:
            self.store.refresh_job_dates(self.ttl)
        self.undo_stack.append(Undo("mark", job_id, {"prev": {"step2": step2, "decision": decision}}))

    def undo(self) -> str | None:
        if self.undo_stack and self.undo_stack[-1].kind == "feedback_many":
            u = self.undo_stack.pop()
            for name, row in u.data["prev"].items():
                if row:
                    self.store.set_feedback(row)
                else:
                    self.store.delete_feedback(u.job_id, name)
            return f"labels on #{u.job_id}"
        if self.undo_stack and self.undo_stack[-1].kind == "mark":
            u = self.undo_stack.pop()
            self.store.set_job_state(u.job_id, **u.data["prev"])
            if self.ttl is not None:
                self.store.refresh_job_dates(self.ttl)
            return f"state of #{u.job_id}"
        return super().undo()


def _verdict(v) -> str:
    """A verdict (or the bare recommend value) in words: recommend / no — dealbreaker / no — doesn't fit / …"""
    if isinstance(v, bool) or v is None:
        return verdict_text(v)
    return verdict_text(v.recommend, v.fit, v.dealbreaker)


def _thumbs(fb: dict) -> str:
    return "👍" * any(fb.values()) + "👎" * (False in fb.values())


def _label_text(v) -> str:
    """Your label, worded as what the filter should do with the job — not a grade of its verdict."""
    if v.label is None:
        return "you: —"
    text = "you: " + ("👍 should recommend" if v.label else "👎 shouldn't recommend")
    if v.right is not None:
        text += " → " + ("it was right" if v.right else ("it missed it" if v.label else "a bad pick"))
    return text + (" (implied by your decision)" if v.label_origin == "implied" else "")


class StateMenu(ModalScreen):
    """Pick a state for one job (`m`)."""

    DEFAULT_CSS = """
    StateMenu { align: center middle; }
    StateMenu OptionList { width: 44; height: auto; border: round $primary; }
    """
    BINDINGS = [Binding("escape", "dismiss(None)", "cancel")]

    def __init__(self, job: dict):
        super().__init__()
        self.job = job

    def compose(self) -> ComposeResult:
        menu = OptionList(*[Option(("• " if self.job.get("state") == s else "  ") + STATE_LABEL[s], id=s)
                            for s in MARKABLE])
        menu.border_title = f"#{self.job['job_id']}: set state"
        yield menu

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option.id)


class JobsScreen(Screen):
    """The Jobs section of `auto-app ui` (or, with audit_mode, the Audit section)."""

    DEFAULT_CSS = """
    #sections { height: 1; padding: 0 1; background: $panel; }
    #tabs { height: 1; padding: 0 1; background: $boost; }
    #main { height: 1fr; }
    #list { width: 3fr; min-width: 50; }
    #detail-box { width: 4fr; border-left: solid $primary; padding: 0 1; }
    #eval { height: auto; max-height: 40%; padding: 0 1; border-bottom: solid $primary; display: none; }
    #eval.show { display: block; }
    #prompt { dock: bottom; display: none; }
    #prompt.show { display: block; }
    #status { height: 1; padding: 0 1; color: $text-muted; }
    """ + KEY_BAR_CSS
    # the key bar at the bottom: the main keys only (? lists every key)
    BAR = [("a", "want to apply", "screen.decide('want_to_apply')"), ("n", "not apply", "screen.decide('not_applied')"),
           ("g", "should recommend", "screen.label(True)"), ("b", "shouldn't", "screen.label(False)"),
           ("/", "search", "screen.search")]
    AUDIT_BAR = [("g", "should recommend", "screen.label(True)"), ("b", "shouldn't", "screen.label(False)"),
                 ("B", "no filter should", "screen.label_all(False)"), ("d", "draw more", "screen.draw"),
                 ("e", "evaluation", "screen.toggle_eval")]
    BINDINGS = [
        # all in the help panel (?); the key bar at the bottom shows the main ones
        Binding("tab", "next_list", "next tab", show=False, priority=True),
        Binding("shift+tab", "prev_list", "previous tab", show=False, priority=True),
        Binding("left", "filter(-1)", "previous filter", show=False, priority=True),
        Binding("right", "filter(1)", "next filter", show=False, priority=True),
        *[Binding(str(i), f"pick({i})", show=False) for i in range(1, 10)],
        Binding("a", "decide('want_to_apply')", "want to apply"),
        Binding("n", "decide('not_applied')", "not apply"),
        Binding("g", "label(True)", "should recommend"),
        Binding("b", "label(False)", "shouldn't"),
        Binding("B", "label_all(False)", "no filter should"),
        Binding("m", "mark", "set state"),
        Binding("u", "undo", "undo"),
        Binding("r", "reason", "reason", show=False),
        Binding("o", "open_link", "open link", show=False),
        Binding("slash", "search", "search"),
        Binding("s", "narrow('source')", "source", show=False),
        Binding("c", "narrow('company')", "company", show=False),
        Binding("d", "draw", "draw more"),
        Binding("e", "toggle_eval", "evaluation"),
        Binding("escape", "cancel", show=False),
        Binding("q", "app.quit", "quit"),
    ]
    COLUMNS = (" ", "#", "company", "title", "posted", "state", "expires", "")
    KEY_HELP = {  # the help panel (?): one line per key
        "next_list": "next tab (the lists: All, Not evaluated, Recommended, …)",
        "prev_list": "previous tab",
        "filter(-1)": "pick the previous filter on this job (marked ▸) — or press its number 1–9",
        "filter(1)": "pick the next filter on this job",
        "decide('want_to_apply')": "want to apply: queue the job for Step 3 (it never expires while queued)",
        "decide('not_applied')": "not apply",
        "label(True)": "the picked filter should recommend this job — your answer about the job, whatever it said",
        "label(False)": "the picked filter shouldn't recommend this job",
        "label_all(False)": "no filter should recommend it (one undo)",
        "mark": "set any state from a menu (like `job mark`)",
        "undo": "undo your last change (any job)",
        "reason": "add a short reason to your last answer on this job",
        "open_link": "open the job's link in your browser",
        "search": "search title / company / source / job number (Esc clears)",
        "narrow('source')": "show only jobs from these sources (comma-separated; empty = all)",
        "narrow('company')": "show only jobs whose company contains this text",
        "draw": "draw more random \"no\" jobs to audit",
        "toggle_eval": "show / hide the evaluation panel",
        "app.quit": "quit",
    }

    def __init__(self, browser: JobBrowser, open_url=webbrowser.open, start: str = "recommended",
                 show_eval: bool = False, audit_mode: bool = False):
        super().__init__()
        self.show_eval = show_eval or audit_mode
        self.audit_mode = audit_mode
        self.tabs = AUDIT_TABS if audit_mode else TABS
        if audit_mode:
            start = "audit"
            self.sub_title = "audit — randomly drawn \"no\" jobs"
        self.browser = browser
        self.open_url = open_url
        self.kind = start
        self.search = ""
        self.rows: list[dict] = []
        self.filter_index = 0
        self.prompt_mode: str | None = None  # search | reason | source | company
        self.just_decided: int | None = None  # the job a / n just moved out of the list (r: a reason for it)
        self.just_decided_row = -1
        self.detail = None

    def compose(self) -> ComposeResult:
        yield Static(id="sections")
        yield Static(id="tabs")
        yield Static(id="eval", classes="show" if self.show_eval else "")
        with Horizontal(id="main"):
            yield DataTable(id="list", cursor_type="row", zebra_stripes=True)
            with VerticalScroll(id="detail-box"):
                yield Static(id="detail")
        yield Static(id="status")
        yield Input(id="prompt")
        yield Static(key_bar(self.AUDIT_BAR if self.audit_mode else self.BAR), id="keys")

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.add_columns(*self.COLUMNS)
        self.reload()
        table.focus()

    def on_screen_resume(self) -> None:
        """Back in this section: another section may have changed the data (e.g. a job queued, labelled)."""
        if self.is_mounted:
            self.reload()

    # ---- drawing --------------------------------------------------------------------------------

    def reload(self, keep_job: int | None = None) -> None:
        table = self.query_one(DataTable)
        if keep_job is None and self.rows and table.cursor_row < len(self.rows):
            keep_job = self.rows[table.cursor_row]["job_id"]
        self.rows = self.browser.jobs(self.kind, self.search)  # a decided job leaves its tab at once (u undoes)
        marks = self.browser.marks()
        table.clear()
        for j in self.rows:
            table.add_row(*self.cells(j, marks.get(j["job_id"], {})), key=str(j["job_id"]))
        ids = [j["job_id"] for j in self.rows]
        if keep_job in ids:
            table.move_cursor(row=ids.index(keep_job))
        self.draw_tabs()
        self.draw_eval()
        self.show_detail()
        refresh = getattr(self.app, "refresh_sections", None)
        if refresh:
            refresh()

    def draw_eval(self) -> None:
        """The evaluation panel (§7.6): recomputed from the database — every label shows at once."""
        if not self.show_eval:
            return
        lines = [line for e in self.browser.evaluation() for line in e.lines()]
        self.query_one("#eval", Static).update(escape("\n".join(lines) or "no filter has judged a job yet"))

    def cells(self, j: dict, fb: dict) -> tuple:
        """One list row; fb = your labels of this job ({filter: good?})."""
        state = "archived" if j.get("archived_at") else STATE_LABEL.get(j["state"], j["state"])
        if "audit" in j:  # the Audit tab: ? to label / ✓ bad for every sampled filter / ✗ good for one (a miss)
            labs = list(j["audit"].values())
            mark = AUDIT_MARK[None] if None in labs else AUDIT_MARK[any(labs)]
        else:
            mark = DECISION_MARK.get(j.get("decision"), " ")
        return (mark, str(j["job_id"]), _cut(j.get("company"), 16),
                _cut(j.get("title"), 40), _date(j.get("posted_at") or j.get("first_seen_at")), state, expiry(j),
                _thumbs(fb))

    def draw_tabs(self) -> None:
        counts = self.browser.counts()
        def title(k: str) -> str:
            n = f"{counts[k]} left of {counts['audit_total']}" if k == "audit" else counts[k]
            return f"{TAB_TITLES[k]} ({n})"

        # each tab is clickable: [@click=screen.goto('…')]
        parts = [(f"[reverse] {title(k)} [/]" if k == self.kind else f"[@click=screen.goto('{k}')] {title(k)} [/]")
                 for k in self.tabs]
        b = self.browser
        notes = [f"source: {', '.join(b.sources)}" if b.sources else "", f"company: {b.company}" if b.company else "",
                 f"filter: {', '.join(sorted(b.only))}" if b.only else "", f"search: {self.search}" if self.search else ""]
        note = "   " + escape("  ".join(n for n in notes if n)) if any(notes) else ""
        self.query_one("#tabs", Static).update(" ".join(parts) + note)

    def current(self) -> dict | None:
        table = self.query_one(DataTable)
        return self.rows[table.cursor_row] if self.rows and table.cursor_row < len(self.rows) else None

    def show_detail(self) -> None:
        job = self.current()
        box = self.query_one("#detail", Static)
        if job is None:
            self.detail = None
            box.update("No jobs in this tab." + (" (search active — Esc clears it)" if self.search else ""))
            return
        d = self.detail = self.browser.detail(job["job_id"], job)
        if self.filter_index >= len(d.verdicts):
            self.filter_index = 0
        store = self.browser.store
        out = [escape(format_job(d.job, store.job_postings(job["job_id"]), store.job_keys_of(job["job_id"]))), "",
               "[b]Filters[/b] " + "─" * 40]
        if not d.verdicts:
            out.append("[dim]No filter has judged this job.[/]")
        for i, v in enumerate(d.verdicts):
            pick = "▸" if i == self.filter_index and len(d.verdicts) > 1 else " "
            old = "" if v.current else " [dim](older version)[/]"
            out.append(f"{pick}[b][{i + 1}] {escape(v.filter)}[/b]   {_verdict(v)} · "
                       f"{_label_text(v)}{old}")
            if v.reason:
                out.append(f"    {escape(v.reason)}")
            for lk in v.lookups[:6]:
                out.append(f"    [dim]looked up: {escape(lk)}[/]")
        out += ["", "[b]Description[/b] " + "─" * 40, escape(d.description or "(no description)")]
        box.update("\n".join(out))
        self.query_one("#detail-box").scroll_home(animate=False)

    def status(self, text: str) -> None:
        self.last_status = text
        self.query_one("#status", Static).update(text)

    def on_data_table_row_highlighted(self, _event) -> None:
        self.show_detail()

    # ---- actions --------------------------------------------------------------------------------

    def check_action(self, action: str, parameters) -> bool | None:
        if self.app.screen is not self:
            return False  # a pop-up (the state menu) is open: its own keys only
        # while typing a search, reason, source or company, keys go to the text box (tab, arrows, letters)
        if self.prompt_mode is not None and action not in ("cancel", "quit"):
            return False
        # each app's footer lists only what's involved in it (a disabled key is hidden and does nothing)
        if action in AUDIT_ONLY and not self.audit_mode:
            return False  # audit samples are drawn and labelled in `filter audit` only
        if action in BROWSE_ONLY and self.audit_mode:
            return False  # an audit labels the drawn jobs; deciding on jobs belongs to `job browse`
        return True

    def _blocked(self, job: dict | None) -> bool:
        """Decisions and states: not on archived jobs (labels are fine — they're training data)."""
        if job and job.get("archived_at"):
            self.status("archived: you can label it (g / b) but not decide on it or change its state")
            return True
        return job is None

    def _switch(self, step: int) -> None:
        self.kind = self.tabs[(self.tabs.index(self.kind) + step) % len(self.tabs)]
        self.rows = []
        self.reload()
        self.query_one(DataTable).move_cursor(row=0)
        self.show_detail()

    def action_goto(self, kind: str) -> None:
        """A click on a tab."""
        if kind in self.tabs and kind != self.kind and self.prompt_mode is None:
            self._switch(self.tabs.index(kind) - self.tabs.index(self.kind))

    def action_next_list(self) -> None:
        self._switch(1)

    def action_prev_list(self) -> None:
        self._switch(-1)

    def action_filter(self, step: int) -> None:
        if self.detail and self.detail.verdicts:
            self.filter_index = (self.filter_index + step) % len(self.detail.verdicts)
            self.show_detail()

    def action_pick(self, n: int) -> None:
        if self.detail and 1 <= n <= len(self.detail.verdicts):
            self.filter_index = n - 1
            self.show_detail()

    def action_label(self, good: bool) -> None:
        job = self.current()
        if job is None:
            return
        if not self.detail or not self.detail.verdicts:
            self.status("No filter judged this job — nothing to label.")
            return
        v = self.detail.verdicts[self.filter_index]
        self.browser.label(job["job_id"], v.filter, good)
        self.status(f"#{job['job_id']}: {v.filter} {'should' if good else 'should not'} recommend it "
                    f"(it said {_verdict(v)}) — saved")
        self._after_label(job["job_id"])

    def action_label_all(self, good: bool) -> None:
        job = self.current()
        if job is None or not self.detail or not self.detail.verdicts:
            return
        names = self.browser.label_all(job["job_id"], good)
        self.status(f"#{job['job_id']}: {'should' if good else 'should not'} be recommended by "
                    f"{', '.join(names)} — saved")
        self._after_label(job["job_id"])

    def _after_label(self, job_id: int) -> None:
        """In the Audit tab a labelled job moves down the list (still-to-label jobs come first), so don't follow it:
        stay on it while one of its sampled filters is unlabelled (that filter picked), else go to the next
        still-to-label job — the next one after it in the list as it was."""
        if self.kind != "audit":
            self.reload(job_id)
            return
        before = [j["job_id"] for j in self.rows]
        self.reload(job_id)
        status = {j["job_id"]: j["audit"] for j in self.rows}
        todo = [f for f, lab in status.get(job_id, {}).items() if lab is None]
        if todo:  # this job still needs a label for another filter
            names = [v.filter for v in self.detail.verdicts] if self.detail else []
            if todo[0] in names:
                self.filter_index = names.index(todo[0])
                self.show_detail()
            return
        at = before.index(job_id) if job_id in before else -1
        order = before[at + 1:] + before[:at]
        nxt = next((j for j in order if None in status.get(j, {}).values()), None)
        if nxt is None:
            self.status(f"{getattr(self, 'last_status', '')} · all labelled — d draws more")
            return
        self.query_one(DataTable).move_cursor(row=[j["job_id"] for j in self.rows].index(nxt))

    def action_draw(self) -> None:
        lines = self.browser.draw()
        self.status("drew more to audit — " + "; ".join(lines) if lines else "no filter has judged a job yet")
        self.reload()

    def action_toggle_eval(self) -> None:
        self.show_eval = not self.show_eval
        self.query_one("#eval", Static).set_class(self.show_eval, "show")
        self.draw_eval()

    def action_decide(self, decision: str) -> None:
        job = self.current()
        if self._blocked(job):
            return
        try:
            self.browser.decide(job["job_id"], decision)
        except ValueError as e:
            self.status(str(e))
            return
        self.status(f"#{job['job_id']}: {STATE_LABEL[decision]} — saved" +
                    (" → Apply (F3) → Queued" if decision == "want_to_apply" else "") + " · r adds a reason · u undoes")
        self.just_decided = job["job_id"]
        self.reload(job["job_id"])
        table = self.query_one(DataTable)
        still = [j["job_id"] for j in self.rows].count(job["job_id"])
        if still and table.cursor_row < len(self.rows) - 1:
            table.move_cursor(row=table.cursor_row + 1)  # still listed here (All, All recommended): on to the next
        # gone from this tab (New recommended): the cursor is already on the next job
        self.just_decided, self.just_decided_row = job["job_id"], table.cursor_row

    def action_mark(self) -> None:
        job = self.current()
        if self._blocked(job):
            return

        def done(to: str | None) -> None:
            if not to:
                return
            try:
                self.browser.mark(job["job_id"], to)
            except ValueError as e:
                self.status(str(e))
                return
            self.status(f"#{job['job_id']}: {STATE_LABEL[to]} — saved · u undoes")
            self.reload(job["job_id"])

        self.app.push_screen(StateMenu(job), done)

    def action_undo(self) -> None:
        what = self.browser.undo()
        self.status(f"undone: {what}" if what else "nothing to undo")
        self.reload()

    def action_open_link(self) -> None:
        job = self.current()
        url = (self.detail.job.get("canonical_url") if self.detail else None) or (job or {}).get("canonical_url")
        if url:
            self.open_url(url)
            self.status(f"opened {url}")
        else:
            self.status("this job has no link")

    # ---- text prompts -----------------------------------------------------------------------------

    def _ask(self, mode: str, placeholder: str, value: str = "") -> None:
        self.prompt_mode = mode
        box = self.query_one("#prompt", Input)
        box.placeholder, box.value = placeholder, value
        box.add_class("show")
        box.focus()

    def action_search(self) -> None:
        self._ask("search", "search title / company / source / #id — Enter to apply, Esc to clear", self.search)

    def action_reason(self) -> None:
        job = self._reason_job()
        if job:
            self._ask("reason", f"a reason for your last answer on #{job} — Enter to save, Esc to cancel")

    def _reason_job(self) -> int | None:
        """r right after a / n: the job just decided (it has left the list); otherwise the job under the cursor."""
        if self.just_decided is not None and self.query_one(DataTable).cursor_row == self.just_decided_row:
            return self.just_decided  # still where the decision left the cursor
        cur = self.current()
        return cur["job_id"] if cur else None

    def action_narrow(self, what: str) -> None:
        current = ", ".join(self.browser.sources) if what == "source" else self.browser.company
        hint = "sources, comma-separated" if what == "source" else "company name contains"
        self._ask(what, f"{hint} — Enter to apply (empty = all), Esc to cancel", current)

    def _close_prompt(self) -> None:
        self.prompt_mode = None
        box = self.query_one("#prompt", Input)
        box.remove_class("show")
        self.query_one(DataTable).focus()

    def action_cancel(self) -> None:
        if self.prompt_mode == "search" or (self.prompt_mode is None and self.search):
            self.search = ""
            self.reload()
        self._close_prompt()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        mode, text = self.prompt_mode, event.value.strip()
        self._close_prompt()
        if mode == "reason":
            job = self._reason_job()
            self.just_decided = None
            got = self.browser.add_reason(job, text) if job else None
            self.status(f"reason saved on your {got}" if got else "answer this job first (a / n / g / b), then add a reason")
            return
        if mode == "search":
            self.search = text
        elif mode == "source":
            self.browser.sources = [x.strip() for x in text.split(",") if x.strip()]
        elif mode == "company":
            self.browser.company = text
        else:
            return
        self.reload()
        self.query_one(DataTable).move_cursor(row=0)
        self.show_detail()


def check_themes(ui: UiConfig) -> list[str]:
    """Theme names in the settings that Textual doesn't know."""
    from textual.theme import BUILTIN_THEMES

    names = [ui.light_theme, ui.dark_theme] + ([] if ui.theme == "auto" else [ui.theme])
    return [n for n in names if n not in BUILTIN_THEMES]
