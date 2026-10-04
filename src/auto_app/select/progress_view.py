"""How `filter run` shows its progress.

In a terminal: each finished judgment is printed as `[n/N] #28 ml-engineer recommend — reason`, under a live bar
at the bottom — done / total, which jobs are running now, cost so far, elapsed and estimated time left.
Piped / logged (or with --stream, whose text would fight the bar): the same `[n/N]` lines, no bar.
"""

import threading
import time

from rich.console import Console
from rich.markup import escape
from rich.progress import BarColumn, MofNCompleteColumn, Progress, TextColumn, TimeElapsedColumn, TimeRemainingColumn

from auto_app.select.engine import Progress as ProgressSink


def _money(usd: float) -> str:
    return f"${usd:.2f}" if usd >= 0.01 or usd == 0 else f"${usd:.4f}"


def _counter(n: int, total: int) -> str:
    w = len(str(total))
    return f"[{n:>{w}}/{total}]"


class LineProgress(ProgressSink):
    """`[n/N]` lines; a start line with the plan."""

    def __init__(self, echo=print):
        self.echo = echo
        self.t0 = 0.0

    def start(self, total: int, jobs: int, parallel: int) -> None:
        self.t0 = time.monotonic()
        self.echo(f"judging {total} runs on {jobs} jobs, {parallel} at a time")

    def done(self, job_id, flt, line: str, n: int, total: int, spent: float) -> None:
        self.echo(f"{_counter(n, total)} {line}")

    def finish(self) -> None:
        if self.t0:
            secs = int(time.monotonic() - self.t0)
            self.echo(f"took {secs // 60}:{secs % 60:02d}")


class LiveProgress(ProgressSink):
    """A live bar under the printed lines (rich)."""

    def __init__(self, console: Console | None = None):
        self.console = console or Console()
        self.running: dict[tuple[int, str], float] = {}  # (job, filter) → started
        self.lock = threading.Lock()
        self.spent = 0.0
        self.bar: Progress | None = None
        self.task = None

    def _running_text(self) -> str:
        with self.lock:
            now = time.monotonic()
            items = sorted(self.running.items(), key=lambda kv: kv[1])
        if not items:
            return ""
        shown = " ".join(f"#{j}({int(now - t)}s)" for (j, _), t in items[:4])
        more = f" +{len(items) - 4}" if len(items) > 4 else ""
        return f"running {shown}{more}"

    def _update(self) -> None:
        if self.bar is not None:
            self.bar.update(self.task, running=escape(self._running_text()), spent=_money(self.spent))

    def start(self, total: int, jobs: int, parallel: int) -> None:
        self.console.print(f"judging {total} runs on {jobs} jobs, {parallel} at a time")
        self.bar = Progress(
            TextColumn("judging"), BarColumn(bar_width=30), MofNCompleteColumn(),
            TextColumn("· {task.fields[spent]} ·"), TimeElapsedColumn(), TextColumn("· left"), TimeRemainingColumn(),
            TextColumn("{task.fields[running]}", style="dim"),
            console=self.console, transient=True, refresh_per_second=4)
        self.task = self.bar.add_task("judging", total=total, running="", spent=_money(0))
        self.bar.start()
        self._tick = threading.Event()
        threading.Thread(target=self._ticker, daemon=True).start()  # keeps the running jobs' seconds moving

    def _ticker(self) -> None:
        while not self._tick.wait(1):
            self._update()

    def began(self, job_id, flt) -> None:
        with self.lock:
            self.running[(job_id, flt.name)] = time.monotonic()
        self._update()

    def done(self, job_id, flt, line: str, n: int, total: int, spent: float) -> None:
        with self.lock:
            self.running.pop((job_id, flt.name), None)
        self.spent = spent
        if self.bar is not None:
            self.bar.console.print(escape(f"{_counter(n, total)} {line}"), highlight=False)
            self.bar.update(self.task, completed=n)
        self._update()

    def finish(self) -> None:
        if self.bar is not None:
            self._tick.set()
            elapsed = self.bar.tasks[0].elapsed or 0
            self.bar.stop()
            self.console.print(f"took {int(elapsed) // 60}:{int(elapsed) % 60:02d}")
            self.bar = None


def make_progress(stream: bool, echo, console: Console | None = None) -> ProgressSink:
    console = console or Console()
    if stream or not console.is_terminal:
        return LineProgress(echo)
    return LiveProgress(console)
