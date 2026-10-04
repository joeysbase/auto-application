"""The Inbox section of `auto-app ui` (`auto-app apply inbox` opens on it; docs/step3-apply.md §6) — what needs you
in Step 3.

A1 tabs: **Hand-offs** (Simplify / manual: open it, apply, say how it went), **Failed** (every applier tried),
**Done** (submitted or dropped). Needs input, Action needed and Ready to submit come with the automated appliers.
Each tab's footer shows only its own keys.
"""

import webbrowser

from rich.markup import escape
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Static

from auto_app.apply.queue import Applications
from auto_app.process.jobs_view import _cut, _date

TABS = ("handoff", "failed", "done")
TAB_TITLES = {"handoff": "Hand-offs", "failed": "Failed", "done": "Done"}
TAB_STATUSES = {"handoff": ("handoff",), "failed": ("failed",), "done": ("submitted", "dropped")}
TAB_KEYS = {"handoff": {"open", "applied", "couldnt", "later", "drop"}, "failed": {"retry", "manual", "drop"},
            "done": {"open"}}


class InboxScreen(Screen):
    """The Inbox section of `auto-app ui`."""

    DEFAULT_CSS = """
    #sections { height: 1; padding: 0 1; background: $panel; }
    #tabs { height: 1; padding: 0 1; background: $boost; }
    #main { height: 1fr; }
    #list { width: 3fr; min-width: 50; }
    #detail-box { width: 4fr; border-left: solid $primary; padding: 0 1; }
    #status { height: 1; padding: 0 1; color: $text-muted; }
    """
    BINDINGS = [
        Binding("tab", "switch(1)", "next tab", show=False, priority=True),
        Binding("shift+tab", "switch(-1)", "previous tab", show=False, priority=True),
        Binding("o", "open", "open"),
        Binding("a", "applied", "applied"),
        Binding("c", "couldnt", "couldn't → next applier"),
        Binding("l", "later", "later"),
        Binding("r", "retry", "retry chain"),
        Binding("m", "manual", "apply by hand"),
        Binding("d", "drop", "drop (not applied)"),
        Binding("q", "app.quit", "quit"),
    ]

    KEY_HELP = {  # the help panel (?)
        "switch(1)": "next tab (Hand-offs, Failed, Done)",
        "switch(-1)": "previous tab",
        "open": "hand-offs: open it (Simplify: in your Chrome, to autofill; manual: the link and your CV) · done: the link",
        "applied": "you applied: the job becomes applied",
        "couldnt": "it didn't work this way: hand it to the next applier in the chain",
        "later": "move it to the back of the list",
        "retry": "start the applier chain over",
        "manual": "apply by hand (the manual applier)",
        "drop": "drop it: the job becomes not applied",
        "app.quit": "quit",
    }

    def __init__(self, apps: Applications, cv=None, open_url=webbrowser.open):
        super().__init__()
        self.apps = apps
        self.cv = cv
        self.open_url = open_url
        self.kind = "handoff"
        self.rows: list[dict] = []

    def compose(self) -> ComposeResult:
        yield Static(id="sections")
        yield Static(id="tabs")
        with Horizontal(id="main"):
            yield DataTable(id="list", cursor_type="row", zebra_stripes=True)
            with VerticalScroll(id="detail-box"):
                yield Static(id="detail")
        yield Static(id="status")
        yield Footer(compact=True)

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.add_columns("app", "job", "company", "title", "platform", "applier", "")
        self.reload()
        table.focus()

    def on_screen_resume(self) -> None:
        if self.is_mounted:
            self.reload()

    # ---- drawing ------------------------------------------------------------------------------------

    def reload(self, keep: int | None = None) -> None:
        table = self.query_one(DataTable)
        rows = self.apps.rows(TAB_STATUSES[self.kind])
        if self.kind == "done":
            rows.sort(key=lambda r: r["updated_at"] or r["created_at"], reverse=True)
        else:  # oldest first; "later" moves one to the back
            rows.sort(key=lambda r: r["updated_at"] or r["created_at"])
        self.rows = rows
        table.clear()
        for r in rows:
            when = r["status"] if self.kind == "done" else ""
            table.add_row(str(r["application_id"]), str(r["job_id"]), _cut(r.get("company"), 16),
                          _cut(r.get("title"), 40), r["platform"], r.get("applier") or "—", when,
                          key=str(r["application_id"]))
        ids = [r["application_id"] for r in rows]
        if keep in ids:
            table.move_cursor(row=ids.index(keep))
        counts = {k: len(self.apps.rows(TAB_STATUSES[k])) for k in TABS}
        parts = [(f"[reverse] {TAB_TITLES[k]} ({counts[k]}) [/]" if k == self.kind
                  else f"[@click=screen.goto('{k}')] {TAB_TITLES[k]} ({counts[k]}) [/]") for k in TABS]  # clickable
        self.query_one("#tabs", Static).update(" · ".join(parts))
        self.show_detail()
        self.refresh_bindings()
        refresh = getattr(self.app, "refresh_sections", None)
        if refresh:
            refresh()

    def current(self) -> dict | None:
        table = self.query_one(DataTable)
        return self.rows[table.cursor_row] if self.rows and table.cursor_row < len(self.rows) else None

    def show_detail(self) -> None:
        r = self.current()
        box = self.query_one("#detail", Static)
        if r is None:
            box.update({"handoff": "No hand-offs waiting. `auto-app apply run` hands queued jobs over.",
                        "failed": "Nothing failed.", "done": "Nothing submitted yet."}[self.kind])
            return
        chain = " → ".join(("▸" if i == r["chain_pos"] and r["status"] not in ("failed", "submitted", "dropped") else "")
                           + a for i, a in enumerate(r["chain"]))
        out = [f"[b]job #{r['job_id']}  {escape(r.get('title') or '')}[/b]", escape(r.get("company") or ""),
               f"apply link: {escape(r.get('apply_url') or '—')}",
               f"platform: {r['platform']} · chain: {chain}",
               f"status: {r['status']}" + (f" · {escape(r['note'])}" if r.get("note") else ""), ""]
        hint = {"handoff": {"simplify": "o opens it in your Chrome — use Simplify's autofill, check, submit; then a "
                                        "(applied), c (couldn't: next applier), l (later) or d (drop).",
                            "manual": "o opens the apply link and shows your CV in Finder — apply by hand, then a."},
                "failed": {}, "done": {}}[self.kind].get(r.get("applier") or "", "")
        if self.kind == "failed":
            hint = "r starts the chain over · m applies by hand · d drops it (not applied)."
        if hint:
            out += [f"[dim]{hint}[/]", ""]
        out.append("[b]Attempts[/b] " + "─" * 30)
        for a in self.apps.attempts(r["application_id"]):
            out.append(f"  {_date(str(a['started_at'])[:19], long=True)}  {a['applier']:<10} {a['pass']:<8} "
                       f"{a['outcome']:<12} {escape(a['reason'] or '')}")
        box.update("\n".join(out))

    def status(self, text: str) -> None:
        self.query_one("#status", Static).update(text)

    def on_data_table_row_highlighted(self, _event) -> None:
        self.show_detail()

    # ---- actions ------------------------------------------------------------------------------------

    def check_action(self, action: str, parameters) -> bool | None:
        if self.app.screen is not self:
            return False
        if action in ("switch", "goto", "quit"):
            return True
        return action in TAB_KEYS[self.kind]  # each tab's footer shows only its own keys

    def action_goto(self, kind: str) -> None:
        """A click on a tab."""
        if kind in TABS and kind != self.kind:
            self.action_switch(TABS.index(kind) - TABS.index(self.kind))

    def action_switch(self, step: int) -> None:
        self.kind = TABS[(TABS.index(self.kind) + step) % len(TABS)]
        self.query_one(DataTable).move_cursor(row=0)
        self.reload()

    def _do(self, fn, done: str) -> None:
        r = self.current()
        if r is None:
            return
        result = fn(r["application_id"])
        self.status(f"application {r['application_id']} (job #{r['job_id']}): {done}"
                    + (f" → {result}" if isinstance(result, str) else ""))
        self.reload()

    def action_open(self) -> None:
        """Hand-offs: open it with its applier (Simplify: your Chrome; manual: the link and your CV). Done: the link."""
        r = self.current()
        if r is None:
            return
        if self.kind == "done":
            if r.get("apply_url"):
                self.open_url(r["apply_url"])
                self.status(f"opened {r['apply_url']}")
            return
        self.status(self.apps.open(r["application_id"], self.cv))

    def action_applied(self) -> None:
        self._do(self.apps.applied, "applied — the job is marked applied")

    def action_couldnt(self) -> None:
        self._do(self.apps.couldnt, "next applier")

    def action_later(self) -> None:
        self._do(self.apps.later, "moved to the back")

    def action_drop(self) -> None:
        self._do(self.apps.drop, "dropped — the job is marked not applied")

    def action_retry(self) -> None:
        self._do(self.apps.retry, "chain started over")

    def action_manual(self) -> None:
        self._do(lambda i: self.apps.retry(i, "manual"), "handed to you (manual)")
