"""The Apply section of `auto-app ui` (F3; `auto-app apply inbox` opens on it; docs/step3-apply.md §6) — Step 3,
applying to the jobs you queued, a tab each (the key bar at the bottom shows the tab's main keys; `?` lists them all):

  Queued            jobs you marked a (want to apply), not started  enter start this one · R start all · d not apply
  Needs input       questions only you can answer          enter answer · A answer for every job waiting with it ·
                                                           S answer + save to answers.yaml · n next applier · d drop
  Action needed     a CAPTCHA / login during submission     s submit again (visible browser) · n next applier · d drop
  Ready to submit   filled; every answer and its source     s submit · f fill only (you submit) · e edit · a applied ·
                                                           n next applier · d drop
  Hand-offs         Simplify / manual                      o open · a applied · c couldn't · l later · r start over · d drop
  Failed            every applier tried                    r start over · m by hand · d drop
  Done              submitted or dropped                   o open the link
"""

import re
import threading
import webbrowser

from rich.markup import escape
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.screen import Screen
from textual.widgets import DataTable, Input, OptionList, SelectionList, Static

from auto_app.apply.queue import Applications
from auto_app.process.jobs_view import _cut, _date
from auto_app.ui import KEY_BAR_CSS, key_bar

TABS = ("queued", "needs_input", "action_needed", "ready", "handoff", "failed", "done")
TAB_TITLES = {"queued": "Queued", "needs_input": "Needs input", "action_needed": "Action needed", "ready": "Ready to submit",
              "handoff": "Hand-offs", "failed": "Failed", "done": "Done"}
TAB_STATUSES = {"needs_input": ("needs_input",), "action_needed": ("action_needed",), "ready": ("ready",),
                "handoff": ("handoff",), "failed": ("failed",), "done": ("submitted", "dropped")}
TAB_KEYS = {"queued": {"enter", "open", "drop"},
            "needs_input": {"enter", "answer", "open", "next_applier", "drop"},
            "action_needed": {"submit", "open", "next_applier", "drop"},
            "ready": {"submit", "fill_only", "edit", "applied", "open", "next_applier", "drop"},
            "handoff": {"open", "applied", "couldnt", "later", "retry", "drop"}, "failed": {"retry", "manual", "drop"},
            "done": {"open"}}
# the key bar at the bottom, per tab: its main keys (? lists every key)
BAR = {"queued": [("enter", "start", "screen.enter"), ("R", "start all", "screen.start")],
       "needs_input": [("enter", "answer", "screen.enter"), ("A", "answer for all", "screen.answer('all')"),
                       ("S", "answer + save", "screen.answer('save')")],
       "action_needed": [("s", "submit again", "screen.submit"), ("n", "next applier", "screen.next_applier")],
       "ready": [("s", "submit", "screen.submit"), ("f", "fill only", "screen.fill_only"), ("e", "edit", "screen.edit")],
       "handoff": [("o", "open", "screen.open"), ("a", "applied", "screen.applied"), ("r", "start over", "screen.retry")],
       "failed": [("r", "start over", "screen.retry"), ("m", "by hand", "screen.manual")],
       "done": [("o", "open", "screen.open")]}
EMPTY = {"queued": "Nothing queued — mark jobs a (want to apply) in Jobs (F1).",
         "needs_input": "No questions waiting for you.", "action_needed": "Nothing needs a hand right now.",
         "ready": "Nothing ready to submit.", "handoff": "No hand-offs waiting. `auto-app apply run` starts queued jobs.",
         "failed": "Nothing failed.", "done": "Nothing submitted yet."}


def _answer_text(a) -> str:
    return ", ".join(map(str, a)) if isinstance(a, list) else str(a)



def _not_started(job: dict) -> bool:
    """A queued job with no application yet, or one sent back to the queue (r)."""
    return job["application"] is None or job["application"]["status"] == "queued"

class InboxScreen(Screen):
    """The Apply section of `auto-app ui` (Step 3)."""

    DEFAULT_CSS = """
    #sections { height: 1; padding: 0 1; background: $panel; }
    #tabs { height: 1; padding: 0 1; background: $boost; }
    #main { height: 1fr; }
    #list { width: 3fr; min-width: 50; }
    #detail-box { width: 4fr; border-left: solid $primary; padding: 0 1; }
    #prompt { dock: bottom; display: none; }
    #prompt.show { display: block; }
    #choices, #multi { dock: bottom; display: none; max-height: 14; border: round $accent; }
    #choices.show, #multi.show { display: block; }
    #status { height: 1; padding: 0 1; color: $text-muted; }
    """ + KEY_BAR_CSS
    BINDINGS = [
        Binding("tab", "switch(1)", "next tab", show=False, priority=True),
        Binding("shift+tab", "switch(-1)", "previous tab", show=False, priority=True),
        Binding("enter", "enter", "start / answer", priority=True),  # Queued: start it · Needs input: answer
        Binding("A", "answer('all')", "answer for all", show=False),
        Binding("S", "answer('save')", "answer + save", show=False),
        Binding("s", "submit", "submit"),
        Binding("f", "fill_only", "fill only"),
        Binding("e", "edit", "edit"),
        Binding("o", "open", "open"),
        Binding("a", "applied", "applied"),
        Binding("c", "couldnt", "couldn't → next applier"),
        Binding("n", "next_applier", "next applier"),
        Binding("l", "later", "later"),
        Binding("r", "retry", "start over"),
        Binding("m", "manual", "apply by hand"),
        Binding("d", "drop", "drop (not applied)"),
        Binding("R", "start", "start queued"),
        Binding("escape", "cancel", show=False),
        Binding("q", "app.quit", "quit"),
    ]
    KEY_HELP = {  # the help panel (?)
        "switch(1)": "next tab (Needs input, Action needed, Ready to submit, Hand-offs, Failed, Done)",
        "switch(-1)": "previous tab",
        "enter": "Queued: start this job · Needs input: answer the question marked ▸ (a dropdown: its number or text)",
        "answer('all')": "answer it for every job waiting with the same question",
        "answer('save')": "answer it everywhere and save it to answers.yaml, so it's answered by itself next time",
        "submit": "fill the form in the browser and submit it (Action needed: again, in a visible browser)",
        "fill_only": "fill the form in a visible browser and leave it to you: check, submit, close the window; then a",
        "edit": "change an answer: type its number, a colon, the new answer (e.g. 3: January 2027)",
        "open": "open the apply link (hand-offs: with its applier — Simplify in your Chrome; manual: the link and your CV)",
        "applied": "you applied: the job becomes applied",
        "couldnt": "it didn't work this way: hand it to the next applier in the chain",
        "next_applier": "give it to the next applier in the chain (e.g. the Simplify hand-off)",
        "later": "move it to the back of the list",
        "retry": "start over: back to the queue and started again (the chain from the top)",
        "manual": "apply by hand (the manual applier)",
        "drop": "drop it: the job becomes not applied",
        "start": "start the jobs you queued (want to apply) that haven't started — like `auto-app apply run`",
        "app.quit": "quit",
    }

    def __init__(self, apps: Applications, cv=None, open_url=webbrowser.open):
        super().__init__()
        self.apps = apps
        self.cv = cv
        self.open_url = open_url
        self.kind = "needs_input"
        self.rows: list[dict] = []
        self.prompt_mode: str | None = None  # one | all | save | edit
        self.picking = None  # the question being answered from a list (and the values its list shows)
        self.pick_values: list = []
        self.busy: set[int] = set()  # applications being submitted
        self.starting = False  # R / enter / r: queued jobs being started (one at a time, in the background)
        self.start_thread: threading.Thread | None = None
        self.running: set[int] | None = set()  # job ids being started now (None: every queued job)
        self.now: int | None = None  # the job whose form is being filled right now
        self.done_n = 0  # jobs started since this run of starts began
        self.waiting: list[list[int] | None] = []  # starts asked for meanwhile: they follow, in order
        self._start_lock = threading.Lock()

    def compose(self) -> ComposeResult:
        yield Static(id="sections")
        yield Static(id="tabs")
        with Horizontal(id="main"):
            yield DataTable(id="list", cursor_type="row", zebra_stripes=True)
            with VerticalScroll(id="detail-box"):
                yield Static(id="detail")
        yield Static(id="status")
        yield Input(id="prompt")
        yield OptionList(id="choices")  # a dropdown / radio / checkbox question: pick, don't type
        yield SelectionList(id="multi")  # a multi-select: space ticks, enter saves
        yield Static(id="keys")

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.add_columns("app", "job", "company", "title", "platform", "applier", "")
        counts = self.counts()  # open on the first tab with something
        self.kind = next((k for k in TABS if counts[k]), "queued")
        self.reload()
        table.focus()

    def on_screen_resume(self) -> None:
        if self.is_mounted:
            self.reload()

    # ---- drawing ------------------------------------------------------------------------------------

    def counts(self) -> dict[str, int]:
        out = {k: len(self.apps.rows(TAB_STATUSES[k])) for k in TABS if k != "queued"}
        out["queued"] = self.not_started()
        return out

    def queued_rows(self) -> list[dict]:
        """Jobs you queued that have no application yet — shaped like application rows for the list."""
        return [{"application_id": None, "job_id": j["job_id"], "company": j.get("company"), "title": j.get("title"),
                 "platform": j["platform"], "chain": j["chain"], "apply_url": j.get("canonical_url"), "applier": None}
                for j in self.apps.queue() if _not_started(j)]

    def reload(self, keep: int | None = None) -> None:
        table = self.query_one(DataTable)
        key = "job_id" if self.kind == "queued" else "application_id"
        if keep is None and (cur := self.current()):
            keep = cur.get(key)
        if self.kind == "queued":
            self.rows = self.queued_rows()
            table.clear()
            for r in self.rows:
                table.add_row("—", str(r["job_id"]), _cut(r.get("company"), 16), _cut(r.get("title"), 40), r["platform"],
                              " → ".join(r["chain"])[:24], self._start_state(r["job_id"]), key=f"j{r['job_id']}")
            ids = [r["job_id"] for r in self.rows]
            if keep in ids:
                table.move_cursor(row=ids.index(keep))
            self._draw_tabs()
            return
        rows = self.apps.rows(TAB_STATUSES[self.kind])
        if self.kind == "done":
            rows.sort(key=lambda r: r["updated_at"] or r["created_at"], reverse=True)
        else:  # oldest first; "later" moves one to the back
            rows.sort(key=lambda r: r["updated_at"] or r["created_at"])
        self.rows = rows
        table.clear()
        for r in rows:
            extra = r["status"] if self.kind == "done" else ("submitting…" if r["application_id"] in self.busy else "")
            if self.kind == "done" and r.get("job_decision") == "want_to_apply":
                extra += " · re-queued"  # you set the job back to want to apply: it's in Queued again
            if self.kind == "needs_input":
                extra = f"{sum(q.open for q in self.apps.questions(r['application_id']))} open"
            table.add_row(str(r["application_id"]), str(r["job_id"]), _cut(r.get("company"), 16),
                          _cut(r.get("title"), 40), r["platform"], r.get("applier") or "—", extra,
                          key=str(r["application_id"]))
        ids = [r["application_id"] for r in rows]
        if keep in ids:
            table.move_cursor(row=ids.index(keep))
        self._draw_tabs()

    def _draw_tabs(self) -> None:
        counts = self.counts()
        parts = [(f"[reverse] {TAB_TITLES[k]} ({counts[k]}) [/]" if k == self.kind
                  else f"[@click=screen.goto('{k}')] {TAB_TITLES[k]} ({counts[k]}) [/]") for k in TABS]  # clickable
        note = ("   [b]starting…[/b]" + (f" ({len(self.waiting)} more waiting)" if self.waiting else "")) if self.starting else ""
        self.query_one("#tabs", Static).update(" ".join(parts) + note)
        self.query_one("#keys", Static).update(key_bar(BAR[self.kind]))
        self.show_detail()
        self.refresh_bindings()
        refresh = getattr(self.app, "refresh_sections", None)
        if refresh:
            refresh()

    def current(self) -> dict | None:
        table = self.query_one(DataTable)
        return self.rows[table.cursor_row] if self.rows and table.cursor_row < len(self.rows) else None

    def target(self, r: dict):
        """The question the answer keys act on: the first open one."""
        return next((q for q in self.apps.questions(r["application_id"]) if q.open), None)

    def show_detail(self) -> None:
        r = self.current()
        box = self.query_one("#detail", Static)
        if r is None:
            box.update(EMPTY[self.kind])
            return
        if self.kind == "queued":
            box.update("\n".join([
                f"[b]job #{r['job_id']}  {escape(r.get('title') or '')}[/b]", escape(r.get("company") or ""),
                f"apply link: {escape(r.get('apply_url') or '—')}",
                f"platform: {r['platform']} · applier chain: {' → '.join(r['chain'])}", "",
                "[dim]Queued (want to apply), not started yet. enter starts this one · R starts every queued job · "
                "d takes it out of the queue (not applied) · o opens the link. Starting asks Claude to answer the "
                "form's questions (Greenhouse) or hands it to you (Simplify / manual).[/]"]))
            return
        chain = " → ".join(("▸" if i == r["chain_pos"] and r["status"] not in ("failed", "submitted", "dropped") else "")
                           + a for i, a in enumerate(r["chain"]))
        out = [f"[b]job #{r['job_id']}  {escape(r.get('title') or '')}[/b]", escape(r.get("company") or ""),
               f"apply link: {escape(r.get('apply_url') or '—')}",
               f"platform: {r['platform']} · chain: {chain}",
               f"status: {r['status']}" + (f" · {escape(r['note'])}" if r.get("note") else ""), ""]
        hint = {"handoff": {"simplify": "o opens it in your Chrome — use Simplify's autofill, check, submit; then a "
                                        "(applied), c (couldn't: next applier), l (later) or d (drop).",
                            "manual": "o opens the apply link and shows your CV in Finder — apply by hand, then a."
                            }.get(r.get("applier") or "", ""),
                "failed": "r starts the chain over · m applies by hand · d drops it (not applied).",
                "needs_input": "enter answers the question marked ▸ (A: for every job asking it · S: and save it).",
                "ready": "Check every answer (✎ = drafted by Claude). s submits · f fills it for you to submit · "
                         "e edits (e.g. 3: new answer).",
                "action_needed": "s submits again in a visible browser — solve the CAPTCHA / log in there.",
                "done": ""}[self.kind]
        if hint:
            out += [f"[dim]{hint}[/]", ""]
        qs = self.apps.questions(r["application_id"])
        if qs:
            target = next((q for q in qs if q.open), None)
            out.append("[b]Questions[/b] " + "─" * 30)
            for i, q in enumerate(qs, start=1):
                if q.source == "skipped":
                    continue
                mark = "▸?" if q is target else ("?" if q.open else ("✎" if q.source == "draft" else "✓"))
                opts = f"  [dim][{' / '.join(o['label'] for o in q.options)[:80]}][/]" if q.options and q.open else ""
                ans = escape(_answer_text(q.answer))[:300] if q.answer not in (None, "", []) else "—"
                src = q.source or ("sensitive — only from answers.yaml or you" if q.kind == "sensitive" else "")
                conf = f" {q.confidence:.2f}" if q.confidence not in (None, 1.0) else ""
                req = "*" if q.required else ""
                out.append(f"{i:>2} {mark} {escape(q.label)}{req}{opts}")
                out.append(f"      {ans}   [dim]{escape(src)}{conf}{(' · ' + escape(q.note)) if q.note and q.open else ''}[/]")
            if self.kind == "ready":
                self.apps.mark_seen(r["application_id"])  # drafts have now been shown to you
        out.append("")
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
        if self.prompt_mode is not None:
            return action in ("cancel", "quit")  # typing an answer: keys go to the text box
        if action in ("switch", "goto", "quit", "cancel", "start"):
            return True
        return action in TAB_KEYS[self.kind]  # each tab's footer shows only its own keys

    def action_goto(self, kind: str) -> None:
        """A click on a tab."""
        if kind in TABS and kind != self.kind:
            self.action_switch(TABS.index(kind) - TABS.index(self.kind))

    def action_switch(self, step: int) -> None:
        self.kind = TABS[(TABS.index(self.kind) + step) % len(TABS)]
        self.rows = []
        self.query_one(DataTable).move_cursor(row=0)
        self.reload()

    def _do(self, fn, done: str) -> None:
        r = self.current()
        if r is None:
            return
        try:
            result = fn(r["application_id"])
        except ValueError as e:
            self.status(str(e))
            return
        self.status(f"application {r['application_id']} (job #{r['job_id']}): {done}"
                    + (f" → {result}" if isinstance(result, str) else ""))
        self.reload()

    def action_open(self) -> None:
        r = self.current()
        if r is None:
            return
        if self.kind == "handoff":
            self.status(self.apps.open(r["application_id"], self.cv))
        elif r.get("apply_url"):
            self.open_url(r["apply_url"])
            self.status(f"opened {r['apply_url']}")

    def action_applied(self) -> None:
        self._do(self.apps.applied, "applied → moved to the Done tab (and Jobs, F1 → Applied)")

    def action_couldnt(self) -> None:
        self._do(self.apps.couldnt, "next applier")

    def action_next_applier(self) -> None:
        self._do(lambda i: self.apps.couldnt(i, "you passed it on"), "next applier")

    def action_later(self) -> None:
        self._do(self.apps.later, "moved to the back")

    def action_drop(self) -> None:
        if self.kind == "queued":  # not started yet: just take it out of the queue
            r = self.current()
            if r:
                self.apps._job(r["job_id"], "not_applied")
                self.status(f"job #{r['job_id']}: taken out of the queue (not applied)")
                self.reload()
            return
        self._do(self.apps.drop, "dropped → moved to the Done tab (the job is marked not applied: Jobs, F1 → Not applied)")

    def action_enter(self) -> None:
        if self.kind == "queued":
            r = self.current()
            if r:
                self.action_start([r["job_id"]])
        elif self.kind == "needs_input":
            self.action_answer("one")

    def action_retry(self) -> None:
        """r (Hand-offs, Failed): back to the queue and started again in the background — the chain from the top."""
        r = self.current()
        if r is None or r.get("application_id") is None:
            return
        self.apps.requeue(r["application_id"])
        self.action_start([r["job_id"]])

    def action_manual(self) -> None:
        self._do(lambda i: self.apps.retry(i, "manual"), "handed to you (manual)")

    # submitting runs in a thread (a browser opens); the list shows "submitting…" meanwhile

    def _start_submit(self, fill_only: bool) -> None:
        r = self.current()
        if r is None or r["application_id"] in self.busy:
            return
        app_id = r["application_id"]
        self.busy.add(app_id)
        wait = f" after job {self.now} has started (one browser at a time)" if self.starting and self.now else ""
        self.status(f"application {app_id}: {'filling it for you' if fill_only else 'submitting'} — a browser opens{wait}…")
        self.reload()

        def work():
            try:
                result = self.apps.submit(app_id, fill_only=fill_only)
            except Exception as e:
                result = f"error: {e}"
            self.app.call_from_thread(self._submitted, app_id, result)

        self.run_worker(work, thread=True, exclusive=False)

    def _submitted(self, app_id: int, result: str) -> None:
        self.busy.discard(app_id)
        self.status(f"application {app_id}: {result}"
                    + (" — the job is marked applied" if result == "submitted" else "")
                    + (" — check the browser, then a if you submitted it" if result == "ready" else ""))
        self.reload()

    # R: start the queued jobs (like `apply run`) — in a thread: each Greenhouse job asks Claude once

    def not_started(self) -> int:
        return sum(1 for j in self.apps.queue() if _not_started(j))

    def _start_state(self, job_id: int) -> str:
        """The Queued list's last column: being started now, or waiting for the current start."""
        if not self.starting:
            return ""
        if job_id == self.now:
            return "starting…"
        if self.running is None or job_id in self.running:
            return "next"
        return "waiting" if any(w is None or job_id in w for w in self.waiting) else ""

    def action_start(self, job_ids: list[int] | None = None) -> None:
        n = len(job_ids) if job_ids else self.not_started()
        if not n:
            self.status("nothing to start — queue jobs with a (want to apply) in Jobs (F1)")
            return
        with self._start_lock:
            if self.starting:  # one start at a time (one Simplify profile): this one follows
                covered = self.running is None or None in self.waiting
                if job_ids and not covered:  # a job already starting or waiting isn't added again
                    taken = (self.running or set()) | {j for w in self.waiting if w for j in w}
                    job_ids = [j for j in job_ids if j not in taken]
                if covered or (job_ids is not None and not job_ids):
                    self.status("already starting or waiting — it starts as soon as the browser is free")
                    return
                self.waiting.append(job_ids)
                what = f"job {', '.join(map(str, job_ids))}" if job_ids else "every queued job"
                self.status(f"{what}: starts when the current one is done ({len(self.waiting)} waiting)")
                self.reload()
                return
            self.starting = True
            self.running = set(job_ids) if job_ids else None
        warn = "" if self.apps.ctx else " (your profile isn't ready — `apply profile` — so they'll go to Simplify)"
        self.status(f"starting {n} queued job(s) in the background (about a minute each)…{warn}")
        self.reload()

        self.done_n = 0

        def progress(job_id, status, done, total):
            try:
                self.app.call_from_thread(self._progress, job_id, status)
            except Exception:
                pass  # you quit

        def work():
            result: dict[str, int] = {}
            batch = job_ids
            while True:
                try:
                    for k, v in self.apps.run(batch, progress=progress).items():
                        result[k] = result.get(k, 0) + v
                except Exception as e:
                    result[f"error: {e}"] = result.get(f"error: {e}", 0) + 1
                with self._start_lock:
                    if not self.waiting:  # done — decided under the lock, so a start asked for now isn't lost
                        self.starting, self.running = False, set()
                        break
                    batch = self.waiting.pop(0)
                    self.running = set(batch) if batch else None
                try:
                    self.app.call_from_thread(self.reload)  # the started ones show in their tabs meanwhile
                except Exception:
                    return  # you quit
            try:
                self.app.call_from_thread(self._started, result)
            except Exception:  # you quit meanwhile: what didn't start stays queued for the next R
                pass

        # a daemon thread: quitting never waits for it (Claude can take a while per job)
        self.start_thread = threading.Thread(target=work, daemon=True, name="apply-start")
        self.start_thread.start()

    def _progress(self, job_id: int, status: str | None) -> None:
        """One job of the running starts began (status None) or finished: the status line and the lists follow."""
        left = self.not_started()
        if status is None:
            self.now = job_id
            self.status(f"starting job {job_id} — about a minute (filling the form in the background) · "
                        f"{self.done_n} done, {left - 1} more after it")
        else:
            self.now = None
            self.done_n += 1
            tab = TAB_TITLES.get({"submitted": "done", "dropped": "done"}.get(status, status), status.replace("_", " "))
            self.status(f"job {job_id} → {tab} · {self.done_n} done, {left} to go")
        self.reload()

    def _started(self, counts: dict) -> None:
        self.now = None
        self.status("started: " + (", ".join(f"{n} {k.replace('_', ' ')}" for k, n in sorted(counts.items())) or "nothing"))
        counts = self.counts()
        first = next((k for k in TABS if k != "queued" and counts[k]), self.kind)  # where the started jobs landed
        if first != self.kind:
            self.kind = first
            self.rows = []
            self.query_one(DataTable).move_cursor(row=0)
        self.reload()

    def action_submit(self) -> None:
        self._start_submit(fill_only=False)

    def action_fill_only(self) -> None:
        self._start_submit(fill_only=True)

    # answering questions and editing answers (a text box at the bottom)

    def _ask(self, mode: str, placeholder: str) -> None:
        self.prompt_mode = mode
        box = self.query_one("#prompt", Input)
        box.placeholder, box.value = placeholder, ""
        box.add_class("show")
        box.focus()

    def _close_prompt(self) -> None:
        self.prompt_mode, self.picking = None, None
        for w in ("#prompt", "#choices", "#multi"):
            self.query_one(w).remove_class("show")
        self.query_one(DataTable).focus()

    def _pick(self, mode: str, q, where: str) -> None:
        """A question with fixed answers: a list to pick from (↑↓ enter; a multi-select: space ticks, enter saves)."""
        self.prompt_mode, self.picking = mode, q
        title = f"{q.label[:110]} — for {where}"
        if q.type == "multiselect":
            box = self.query_one("#multi", SelectionList)
            box.clear_options()
            box.add_options([(o["label"], o["label"]) for o in q.options])
            box.highlighted = 0
            box.border_title = title + " · space ticks, enter saves, esc cancels"
        else:
            if q.type == "checkbox" and not q.options:
                labels, self.pick_values = ["Yes — check it", "No — leave it unchecked"], ["yes", "no"]
            else:
                labels = [o["label"] for o in q.options]
                self.pick_values = labels
            box = self.query_one("#choices", OptionList)
            box.clear_options()
            box.add_options(labels)
            box.highlighted = 0
            box.border_title = title + " · ↑↓ enter, esc cancels"
        box.add_class("show")
        box.focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if self.picking is None:
            return
        mode, q, value = self.prompt_mode, self.picking, self.pick_values[event.option_index]
        self._close_prompt()
        self._give(mode, q, value)

    def on_key(self, event) -> None:
        """enter on the multi-select list saves the ticked options (space ticks)."""
        if event.key == "enter" and self.picking is not None and self.picking.type == "multiselect":
            event.stop()
            mode, q = self.prompt_mode, self.picking
            ticked = list(self.query_one("#multi", SelectionList).selected)
            self._close_prompt()
            if ticked:
                self._give(mode, q, ticked)

    def action_cancel(self) -> None:
        self._close_prompt()

    def action_answer(self, how: str) -> None:
        r = self.current()
        q = self.target(r) if r else None
        if q is None:
            self.status("no open question on this application")
            return
        where = {"one": "this job", "all": "every job asking it", "save": "every job + answers.yaml"}[how]
        if q.options or q.type in ("checkbox", "select", "radio", "multiselect"):
            self._pick(how, q, where)
            return
        self._ask(how, f"{q.label[:80]}  (type the answer) — for {where}; Enter saves, Esc cancels")

    def action_edit(self) -> None:
        if self.current():
            self._ask("edit", "number: new answer (e.g. 3: January 2027) — a choice question: just its number, then "
                              "pick — Enter saves, Esc cancels")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        mode, text = self.prompt_mode, event.value.strip()
        self._close_prompt()
        r = self.current()
        if not r or not text:
            return
        qs = self.apps.questions(r["application_id"])
        if mode == "edit" and text.isdigit() and 1 <= int(text) <= len(qs):  # a choice question: pick the new answer
            q = qs[int(text) - 1]
            if q.options or q.type in ("checkbox", "select", "radio", "multiselect"):
                self._pick("edit", q, "this job")
                return
        if mode == "edit":
            m = re.match(r"^(\d+)\s*[:=]\s*(.+)$", text)
            visible = [q for q in qs]
            if not m or not (1 <= int(m.group(1)) <= len(visible)):
                self.status("type the question's number, a colon, and the new answer — e.g. 3: January 2027")
                return
            q, text = visible[int(m.group(1)) - 1], m.group(2)
            how = "one"
        else:
            q = self.target(r)
            if q is None:
                return
        self._give(mode, q, text)

    def _give(self, mode: str, q, value) -> None:
        """Save an answer (typed or picked) and go on to the next open question."""
        r = self.current()
        if r is None:
            return
        how = "one" if mode == "edit" else mode
        text = value if isinstance(value, str) else ""
        if q.options and isinstance(value, str):
            if text.isdigit() and 1 <= int(text) <= len(q.options):
                value = q.options[int(text) - 1]["label"]
            from auto_app.apply.questions import to_option

            if to_option(value, q.options) is None:
                self.status(f"not one of the options: {' / '.join(o['label'] for o in q.options)}")
                if mode != "edit":
                    self.action_answer(how)  # the same question again
                return
        changed = self.apps.answer(r["application_id"], q.id, value, everywhere=how in ("all", "save"), save=how == "save")
        said = (f"answered “{q.label[:50]}” on {len(changed)} application(s)"
                + (" and saved it to answers.yaml" if how == "save" else ""))
        self.reload(keep=r["application_id"])
        self.status(said)
        if mode == "edit" or self.kind != "needs_input":
            return
        # straight on to the next open question: this job's, else (it's ready now) the next job's — Esc stops
        nxt = self.current()
        if nxt is None:
            self.status(said + " — every question answered")
        elif self.target(nxt) is not None:
            if nxt["application_id"] != r["application_id"]:
                self.status(said + f" — job {r['job_id']} is ready to submit; next: job {nxt['job_id']}")
            self.action_answer("one")
