"""`auto-app ui` — every interactive part of the pipeline in one full-screen app (Textual), one section each:

  F1  Jobs    the job pool and your review (`job browse`)          — process/job_browser.py JobsScreen
  F2  Audit   random samples of each filter's "no" jobs (`filter audit`) — JobsScreen in audit mode
  F3  Apply   Step 3: applying to the jobs you queued (`apply inbox`) — apply/inbox.py InboxScreen

Each section keeps its own state (tab, cursor, search) while you're elsewhere, and reloads when you come back. The
bar at the top shows what's waiting in each; the key bar at the bottom shows the current tab's main keys (`?` lists
every key). `job browse`, `filter audit` and `apply inbox` open this app on their section.
"""

import webbrowser

from rich.markup import escape
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.screen import ModalScreen, Screen
from textual.widgets import Static

from auto_app.core.appearance import system_dark
from auto_app.core.config import UiConfig

SECTIONS = ("jobs", "audit", "inbox")
SECTION_TITLES = {"jobs": "Jobs", "audit": "Audit", "inbox": "Apply"}  # "inbox": Step 3, applying
SECTION_KEYS = {"jobs": "F1", "audit": "F2", "inbox": "F3"}
FOLLOW_EVERY_S = 3  # how often theme auto checks the system's mode
KEY_BAR_CSS = "#keys { dock: bottom; height: 1; padding: 0 1; background: $panel; }"
ALWAYS = [("?", "all keys", "app.help"), ("q", "quit", "app.quit")]


def key_bar(items: list[tuple[str, str, str]]) -> str:
    """The bottom line: a few main keys (key, what it does, its action — clickable), then ? all keys · q quit."""
    return "  ".join(f"[@click={action}][b]{escape(key)}[/b] {escape(label)}[/]" for key, label, action in items + ALWAYS)


class MessageScreen(Screen):
    """A section that can't open (e.g. a broken apply policy): says why."""

    DEFAULT_CSS = "#sections { height: 1; padding: 0 1; background: $panel; } #why { padding: 1 2; } " + KEY_BAR_CSS
    BINDINGS = [Binding("q", "app.quit", "quit")]

    def __init__(self, text: str):
        super().__init__()
        self.text = text

    def compose(self) -> ComposeResult:
        yield Static(id="sections")
        yield Static(escape(self.text), id="why")
        yield Static(key_bar([]), id="keys")


class HelpScreen(ModalScreen):
    """`?` — every key of the current section (in its current tab / mode), with what it does."""

    DEFAULT_CSS = """
    HelpScreen { align: center middle; }
    HelpScreen > VerticalScroll { width: 96; max-height: 85%; border: round $primary; padding: 0 1; background: $surface; }
    """
    BINDINGS = [Binding("escape", "dismiss", "close"), Binding("question_mark", "dismiss", "close", show=False),
                Binding("q", "dismiss", "close", show=False)]

    def __init__(self, title: str, rows: list[tuple[str, str]]):
        super().__init__()
        self.title_text, self.rows = title, rows

    def compose(self) -> ComposeResult:
        from textual.containers import VerticalScroll

        width = max((len(k) for k, _ in self.rows), default=4) + 2
        lines = [f"[b]{escape(self.title_text)}[/b] — keys (Esc closes)", ""]
        lines += [f"[b]{escape(k):<{width}}[/b]{escape(text)}" for k, text in self.rows]
        with VerticalScroll():
            yield Static("\n".join(lines))


class AutoApp(App):
    TITLE = "auto-app"
    BINDINGS = [
        # shown in the section bar at the top, not the key bar (which lists the section's own keys)
        Binding("f1", "section('jobs')", "Jobs", priority=True, show=False),
        Binding("f2", "section('audit')", "Audit", priority=True, show=False),
        Binding("f3", "section('inbox')", "Apply", priority=True, show=False),
        # [ / ] — previous / next section: no clash with VS Code's terminal (F3 = find next there); not priority,
        # so they still type into a search or reason box
        Binding("left_square_bracket", "cycle(-1)", "section", show=False),
        Binding("right_square_bracket", "cycle(1)", "section", show=False),
        Binding("question_mark", "help", "keys"),  # not priority: typed as usual in a search box
    ]

    def __init__(self, jobs=None, audit=None, inbox=None, cv=None, start: str = "jobs", jobs_tab: str = "recommended",
                 ui: UiConfig | None = None, dark=system_dark, open_url=webbrowser.open, why: dict | None = None):
        """jobs / audit: JobBrowser (separate ones — narrowing Jobs mustn't narrow the audit); inbox: Applications.
        A section given None shows `why[section]` instead."""
        super().__init__()
        self.sources = {"jobs": jobs, "audit": audit, "inbox": inbox}
        self.cv = cv
        self.start = start
        self.jobs_tab = jobs_tab
        self.ui = ui or UiConfig()
        self.system_dark = dark
        self.last_dark: bool | None = None
        self.open_url = open_url
        self.why = why or {}
        self.section = start

    def _screen(self, name: str) -> Screen:
        src = self.sources[name]
        if src is None:
            return MessageScreen(self.why.get(name, f"{SECTION_TITLES[name]} isn't available"))
        if name == "inbox":
            from auto_app.apply.inbox import InboxScreen

            return InboxScreen(src, cv=self.cv, open_url=self.open_url)
        from auto_app.process.job_browser import JobsScreen

        if name == "audit":
            return JobsScreen(src, open_url=self.open_url, audit_mode=True)
        return JobsScreen(src, open_url=self.open_url, start=self.jobs_tab)

    async def on_mount(self) -> None:
        if self.ui.theme == "auto":
            self.follow_system()
            self.set_interval(FOLLOW_EVERY_S, self.follow_system)
        else:
            self.theme = self.ui.theme
        for name in SECTIONS:
            self.install_screen(self._screen(name), name)
        self.section = self.start
        await self.push_screen(self.start)  # wait until it's mounted, so the section is ready when the app is
        self.refresh_sections()

    def follow_system(self) -> None:
        """theme auto: switch when the system's light / dark mode changes (a theme picked with ctrl+p stays until then)."""
        dark = self.system_dark()
        if dark is None or dark == self.last_dark:
            return
        self.last_dark = dark
        self.theme = self.ui.dark_theme if dark else self.ui.light_theme

    # ---- sections ---------------------------------------------------------------------------------

    def waiting(self) -> dict[str, str]:
        """What's waiting in each section, for the bar."""
        out = {"jobs": "", "audit": "", "inbox": ""}
        audit = self.sources["audit"]
        if audit is not None:
            left = audit.counts()["audit"]
            out["audit"] = f"{left} left" if left else ""
        inbox = self.sources["inbox"]
        if inbox is not None:
            n = {k: len(inbox.rows((k,))) for k in ("needs_input", "action_needed", "ready", "handoff", "failed")}
            from auto_app.apply.inbox import _not_started

            n["queued"] = sum(1 for j in inbox.queue() if _not_started(j))  # the same count as the Queued tab
            words = (("queued", "queued"), ("needs_input", "to answer"), ("action_needed", "need a hand"), ("ready", "ready"),
                     ("handoff", "waiting"), ("failed", "failed"))
            out["inbox"] = ", ".join(f"{n[k]} {w}" for k, w in words if n[k])
        return out

    def refresh_sections(self) -> None:
        waiting = self.waiting()
        parts = []
        for name in SECTIONS:
            label = SECTION_TITLES[name] + (f" ({waiting[name]})" if waiting[name] else "")
            # click a section to go there
            parts.append(f"[reverse] {label} [/]" if name == self.section else f"[@click=app.section('{name}')] {label} [/]")
        text = " · ".join(parts) + "      [dim]" + escape("click, F1 / F2 / F3, or [ ] to switch") + "[/]"
        for name in SECTIONS:
            try:
                self.get_screen(name).query_one("#sections", Static).update(text)
            except Exception:  # not mounted yet
                pass

    def action_section(self, name: str) -> None:
        if name == self.section or self.screen is not self.get_screen(self.section):
            return  # already there, or a pop-up is open
        self.section = name
        self.switch_screen(name)
        self.refresh_sections()

    def help_rows(self, screen) -> list[tuple[str, str]]:
        """Every key the section offers right now (its footer, plus the keys kept off it), explained."""
        rows, seen = [], set()
        explain = getattr(screen, "KEY_HELP", {})
        for b in list(getattr(screen, "BINDINGS", [])):
            name = b.action.split("(")[0].removeprefix("screen.")
            if b.action in seen or name in ("cancel",) or screen.check_action(name, ()) is False:
                continue
            seen.add(b.action)
            text = explain.get(b.action) or explain.get(name) or b.description
            if b.action.startswith("pick("):
                if "pick" in seen:
                    continue
                seen.add("pick")
                rows.append(("1–9", "pick that filter on this job"))
                continue
            rows.append((self.get_key_display(b), text))
        rows += [("click · F1 F2 F3 · [ ]", "switch section: Jobs, Audit, Apply"), ("ctrl+p", "command palette (themes, sections)"),
                 ("?", "this help")]
        return rows

    def action_help(self) -> None:
        if self.screen is not self.get_screen(self.section):
            return  # a pop-up is open
        title = SECTION_TITLES[self.section] + (f" · {self.screen.kind}" if hasattr(self.screen, "kind") else "")
        self.push_screen(HelpScreen(title, self.help_rows(self.screen)))

    def action_quit(self) -> None:
        """q: quit — but warn once if a form is being submitted right now (a second q quits anyway)."""
        inbox = self.get_screen("inbox") if "inbox" in self._installed_screens else None
        if getattr(inbox, "busy", None) and not getattr(self, "quit_armed", False):
            self.quit_armed = True
            msg = "a form is being submitted right now (a browser is open) — wait for it, or press q again to quit anyway"
            if self.screen is inbox:
                inbox.status(msg)
            else:
                self.notify(msg, severity="warning")
            return
        self.exit()

    def action_cycle(self, step: int) -> None:
        self.action_section(SECTIONS[(SECTIONS.index(self.section) + step) % len(SECTIONS)])

    def get_system_commands(self, screen):  # the command palette (ctrl+p): "Go to Jobs / Audit / Apply"
        yield from super().get_system_commands(screen)
        from textual.app import SystemCommand

        for name in SECTIONS:
            yield SystemCommand(f"Go to {SECTION_TITLES[name]}", f"{SECTION_KEYS[name]}",
                                lambda n=name: self.action_section(n))
