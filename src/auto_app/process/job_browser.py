"""`auto-app job browse` — the job pool in a full-screen app (Textual), and where you review Step 2's results
(docs/step2-select.md §8).

Left: the jobs in one tab (All, Not evaluated, Recommended, Un-recommended, Want to apply, Applied, Not applied,
Archived; `tab` switches). Right: the selected job — what `job show` prints, every filter's verdict, reason and
lookups with your feedback, then the description. Keys save at once: `a` want to apply, `n` not apply,
`g` / `b` the picked filter was right / wrong, `m` any state, `u` undo; `s` / `c` / `/` narrow the list.
Your answers go through `Review` (select/review.py); archived records are read-only.
"""

import webbrowser

from rich.markup import escape
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import DataTable, Footer, Input, OptionList, Static
from textual.widgets.option_list import Option

from auto_app.core.appearance import system_dark
from auto_app.core.config import UiConfig
from auto_app.process.jobs_view import STATE_LABEL, _cut, _date, expiry, format_job
from auto_app.select.review import Review, Undo

TABS = ("all", "not_evaluated", "recommended", "un_recommended", "want_to_apply", "applied", "not_applied", "archived")
TAB_TITLES = {"all": "All", "not_evaluated": "Not evaluated", "recommended": "Recommended",
              "un_recommended": "Un-recommended", "want_to_apply": "Want to apply", "applied": "Applied",
              "not_applied": "Not applied", "archived": "Archived"}
MARKABLE = ("want_to_apply", "applied", "not_applied", "recommended", "un_recommended", "not_evaluated")
DECISION_MARK = {"want_to_apply": "★", "not_applied": "✗", "applied": "✓"}
FOLLOW_EVERY_S = 3  # how often theme auto checks the system's mode


class JobBrowser(Review):
    """The data side: jobs per tab, narrowed by source / company / filter / search; `mark` with undo; and
    everything `Review` does (details, decisions, feedback)."""

    def __init__(self, store, filters: list, ttl=None, sources: list[str] | None = None, company: str | None = None,
                 only_filters: list[str] | None = None):
        super().__init__(store, filters, ttl, only_filters)
        self.sources = list(sources or [])
        self.company = company or ""

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
        if kind == "archived":
            rows = self.store.jobs(archived=True)
        else:
            rows = self.store.jobs()
            if kind != "all":
                rows = [j for j in rows if j["state"] == kind or j["job_id"] in keep]
        rows = self._narrow(rows)
        if search:
            q = search.lower()
            rows = [j for j in rows if q in f"{j.get('title') or ''} {j.get('company') or ''} "
                                             f"{' '.join(j.get('sources') or [])} {j['job_id']}".lower()]
        return sorted(rows, key=lambda j: (j.get("posted_at") or j.get("first_seen_at") or "", j["job_id"]), reverse=True)

    def counts(self) -> dict[str, int]:
        active = self._narrow(self.store.jobs())
        out = {k: sum(1 for j in active if j["state"] == k) for k in TABS}
        out["all"] = len(active)
        out["archived"] = len(self._narrow(self.store.jobs(archived=True)))
        return out

    def mark(self, job_id: int, to: str) -> None:
        if to not in MARKABLE:
            raise ValueError(to)
        step2, decision = self.store.mark_job(job_id, to)
        if self.ttl is not None:
            self.store.refresh_job_dates(self.ttl)
        self.undo_stack.append(Undo("mark", job_id, {"prev": {"step2": step2, "decision": decision}}))

    def undo(self) -> str | None:
        if self.undo_stack and self.undo_stack[-1].kind == "mark":
            u = self.undo_stack.pop()
            self.store.set_job_state(u.job_id, **u.data["prev"])
            if self.ttl is not None:
                self.store.refresh_job_dates(self.ttl)
            return f"state of #{u.job_id}"
        return super().undo()


def _verdict(v) -> str:
    return "recommend" if v is True else "no" if v is False else "couldn't read"


def _thumbs(fb: dict) -> str:
    return "👍" * any(fb.values()) + "👎" * (False in fb.values())


def _rating(r, origin=None) -> str:
    if r is None:
        return "(not rated)"
    return ("👍 right" if r else "👎 wrong") + (" (implied by your decision)" if origin == "implied" else "")


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


class JobBrowserApp(App):
    TITLE = "auto-app jobs"
    CSS = """
    #tabs { height: 1; padding: 0 1; background: $boost; }
    #main { height: 1fr; }
    #list { width: 3fr; min-width: 50; }
    #detail-box { width: 4fr; border-left: solid $primary; padding: 0 1; }
    #prompt { dock: bottom; display: none; }
    #prompt.show { display: block; }
    #status { height: 1; padding: 0 1; color: $text-muted; }
    """
    BINDINGS = [
        Binding("tab", "next_list", "tab", priority=True),
        Binding("shift+tab", "prev_list", "tab", show=False, priority=True),
        Binding("left", "filter(-1)", "filter", priority=True),
        Binding("right", "filter(1)", "filter", show=False, priority=True),
        *[Binding(str(i), f"pick({i})", show=False) for i in range(1, 10)],
        Binding("a", "decide('want_to_apply')", "want to apply"),
        Binding("n", "decide('not_applied')", "not apply"),
        Binding("g", "rate(True)", "right"),
        Binding("b", "rate(False)", "wrong"),
        Binding("m", "mark", "set state"),
        Binding("u", "undo", "undo"),
        Binding("r", "reason", "reason"),
        Binding("o", "open_link", "open link"),
        Binding("slash", "search", "search"),
        Binding("s", "narrow('source')", "source"),
        Binding("c", "narrow('company')", "company"),
        Binding("escape", "cancel", show=False),
        Binding("q", "quit", "quit"),
    ]
    COLUMNS = (" ", "#", "company", "title", "posted", "state", "expires", "")

    def __init__(self, browser: JobBrowser, open_url=webbrowser.open, start: str = "all", ui: UiConfig | None = None,
                 dark=system_dark):
        super().__init__()
        self.browser = browser
        self.open_url = open_url
        self.ui = ui or UiConfig()
        self.system_dark = dark  # () -> bool | None; polled while theme is auto
        self.last_dark: bool | None = None
        self.kind = start
        self.search = ""
        self.rows: list[dict] = []
        self.answered = {k: set() for k in TABS}  # changed in this session, per tab: stay there with their mark
        self.filter_index = 0
        self.prompt_mode: str | None = None  # search | reason | source | company
        self.detail = None

    def compose(self) -> ComposeResult:
        yield Static(id="tabs")
        with Horizontal(id="main"):
            yield DataTable(id="list", cursor_type="row", zebra_stripes=True)
            with VerticalScroll(id="detail-box"):
                yield Static(id="detail")
        yield Static(id="status")
        yield Input(id="prompt")
        yield Footer()

    def on_mount(self) -> None:
        if self.ui.theme == "auto":
            self.follow_system()
            self.set_interval(FOLLOW_EVERY_S, self.follow_system)
        else:
            self.theme = self.ui.theme
        table = self.query_one(DataTable)
        table.add_columns(*self.COLUMNS)
        self.reload()
        table.focus()

    def follow_system(self) -> None:
        """theme auto: switch when the system's light / dark mode changes (a theme picked with ctrl+p stays
        until then)."""
        dark = self.system_dark()
        if dark is None or dark == self.last_dark:
            return
        self.last_dark = dark
        self.theme = self.ui.dark_theme if dark else self.ui.light_theme

    # ---- drawing --------------------------------------------------------------------------------

    def reload(self, keep_job: int | None = None) -> None:
        table = self.query_one(DataTable)
        if keep_job is None and self.rows and table.cursor_row < len(self.rows):
            keep_job = self.rows[table.cursor_row]["job_id"]
        self.rows = self.browser.jobs(self.kind, self.search, keep=self.answered[self.kind])
        marks = self.browser.marks()
        table.clear()
        for j in self.rows:
            table.add_row(*self.cells(j, marks.get(j["job_id"], {})), key=str(j["job_id"]))
        ids = [j["job_id"] for j in self.rows]
        if keep_job in ids:
            table.move_cursor(row=ids.index(keep_job))
        self.draw_tabs()
        self.show_detail()

    def cells(self, j: dict, fb: dict) -> tuple:
        """One list row; fb = your ratings of this job ({filter: right?})."""
        state = "archived" if j.get("archived_at") else STATE_LABEL.get(j["state"], j["state"])
        return (DECISION_MARK.get(j.get("decision"), " "), str(j["job_id"]), _cut(j.get("company"), 16),
                _cut(j.get("title"), 40), _date(j.get("posted_at") or j.get("first_seen_at")), state, expiry(j),
                _thumbs(fb))

    def draw_tabs(self) -> None:
        counts = self.browser.counts()
        parts = [(f"[reverse] {TAB_TITLES[k]} ({counts[k]}) [/]" if k == self.kind else f" {TAB_TITLES[k]} ({counts[k]}) ")
                 for k in TABS]
        b = self.browser
        notes = [f"source: {', '.join(b.sources)}" if b.sources else "", f"company: {b.company}" if b.company else "",
                 f"filter: {', '.join(sorted(b.only))}" if b.only else "", f"search: {self.search}" if self.search else ""]
        note = "   " + escape("  ".join(n for n in notes if n)) if any(notes) else ""
        self.query_one("#tabs", Static).update(" · ".join(parts) + note)

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
            out.append(f"{pick}[b][{i + 1}] {escape(v.filter)}[/b]   {_verdict(v.recommend)} · "
                       f"{_rating(v.rating, v.rating_origin)}{old}")
            if v.reason:
                out.append(f"    {escape(v.reason)}")
            for lk in v.lookups[:6]:
                out.append(f"    [dim]looked up: {escape(lk)}[/]")
        out += ["", "[b]Description[/b] " + "─" * 40, escape(d.description or "(no description)")]
        box.update("\n".join(out))
        self.query_one("#detail-box").scroll_home(animate=False)

    def status(self, text: str) -> None:
        self.query_one("#status", Static).update(text)

    def on_data_table_row_highlighted(self, _event) -> None:
        self.show_detail()

    # ---- actions --------------------------------------------------------------------------------

    def check_action(self, action: str, parameters) -> bool | None:
        if len(self.screen_stack) > 1:
            return False  # a pop-up (the state menu) is open: its own keys only
        # while typing a search, reason, source or company, keys go to the text box (tab, arrows, letters)
        if self.prompt_mode is not None and action not in ("cancel", "quit"):
            return False
        return True

    def _blocked(self, job: dict | None) -> bool:
        if job and job.get("archived_at"):
            self.status("archived records are read-only")
            return True
        return job is None

    def _switch(self, step: int) -> None:
        self.kind = TABS[(TABS.index(self.kind) + step) % len(TABS)]
        self.rows = []
        self.reload()
        self.query_one(DataTable).move_cursor(row=0)
        self.show_detail()

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

    def action_rate(self, correct: bool) -> None:
        job = self.current()
        if self._blocked(job):
            return
        if not self.detail or not self.detail.verdicts:
            self.status("No filter judged this job — nothing to rate.")
            return
        v = self.detail.verdicts[self.filter_index]
        self.browser.rate(job["job_id"], v.filter, correct)
        self.status(f"#{job['job_id']}: {v.filter} was {'right' if correct else 'wrong'} ({_verdict(v.recommend)}) — saved")
        self.reload(job["job_id"])

    def action_decide(self, decision: str) -> None:
        job = self.current()
        if self._blocked(job):
            return
        try:
            self.browser.decide(job["job_id"], decision)
        except ValueError as e:
            self.status(str(e))
            return
        self.answered[self.kind].add(job["job_id"])
        self.status(f"#{job['job_id']}: {STATE_LABEL[decision]} — saved" +
                    (" (queued for Step 3)" if decision == "want_to_apply" else ""))
        self.reload(job["job_id"])
        table = self.query_one(DataTable)
        if table.cursor_row < len(self.rows) - 1:
            table.move_cursor(row=table.cursor_row + 1)  # on to the next job

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
            self.answered[self.kind].add(job["job_id"])
            self.status(f"#{job['job_id']}: {STATE_LABEL[to]} — saved")
            self.reload(job["job_id"])

        self.push_screen(StateMenu(job), done)

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
        if not self._blocked(self.current()):
            self._ask("reason", "a reason for your last answer on this job — Enter to save, Esc to cancel")

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
            job = self.current()
            got = self.browser.add_reason(job["job_id"], text) if job else None
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


def run_browser(browser: JobBrowser, start: str = "all", ui: UiConfig | None = None) -> None:
    JobBrowserApp(browser, start=start, ui=ui).run()
