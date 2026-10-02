"""macOS launchd schedule for `auto-app run` (`auto-app schedule install|show|remove`)."""

import os
import plistlib
import shutil
import subprocess
from pathlib import Path

from auto_app.settings import PROJECT_ROOT

LABEL = "com.auto-app.run"
AGENTS = Path.home() / "Library" / "LaunchAgents"


def plist_path() -> Path:
    return AGENTS / f"{LABEL}.plist"


def parse_times(times: list[str]) -> list[dict]:
    out = []
    for t in times:
        h, _, m = t.partition(":")
        hour, minute = int(h), int(m or 0)
        if not (0 <= hour < 24 and 0 <= minute < 60):
            raise ValueError(f"not a time of day: {t!r} (use HH:MM)")
        out.append({"Hour": hour, "Minute": minute})
    return out


def build_plist(times: list[str], *, headless: bool, uv: str | None = None, project: Path = PROJECT_ROOT) -> dict:
    uv = uv or shutil.which("uv") or "/opt/homebrew/bin/uv"
    args = [uv, "run", "--project", str(project), "auto-app", "run", "--scheduled", "--purge"]
    if headless:
        args.append("--headless")
    log = project / "data" / "logs" / "scheduled.log"
    return {
        "Label": LABEL,
        "ProgramArguments": args,
        "WorkingDirectory": str(project),
        "StartCalendarInterval": parse_times(times),
        "StandardOutPath": str(log),
        "StandardErrorPath": str(log),
        "EnvironmentVariables": {"PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"},
        "ProcessType": "Background",
    }


def _launchctl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["launchctl", *args], capture_output=True, text=True)


def install(plist: dict) -> str:
    AGENTS.mkdir(parents=True, exist_ok=True)
    Path(plist["StandardOutPath"]).parent.mkdir(parents=True, exist_ok=True)
    plist_path().write_bytes(plistlib.dumps(plist))
    domain = f"gui/{os.getuid()}"
    _launchctl("bootout", f"{domain}/{LABEL}")  # replace an earlier schedule, if any
    r = _launchctl("bootstrap", domain, str(plist_path()))
    if r.returncode != 0:
        raise RuntimeError(f"launchctl bootstrap failed: {r.stderr.strip() or r.stdout.strip()}")
    return str(plist_path())


def remove() -> bool:
    _launchctl("bootout", f"gui/{os.getuid()}/{LABEL}")
    if plist_path().exists():
        plist_path().unlink()
        return True
    return False


def show() -> str:
    if not plist_path().exists():
        return "no schedule installed (`auto-app schedule install --at 09:00`)"
    plist = plistlib.loads(plist_path().read_bytes())
    times = ", ".join(f"{t['Hour']:02d}:{t['Minute']:02d}" for t in plist.get("StartCalendarInterval", []))
    loaded = _launchctl("print", f"gui/{os.getuid()}/{LABEL}").returncode == 0
    return "\n".join([
        f"schedule     {times} daily ({'loaded' if loaded else 'NOT loaded — run `auto-app schedule install` again'})",
        f"command      {' '.join(plist['ProgramArguments'])}",
        f"log          {plist['StandardOutPath']}",
        f"plist        {plist_path()}",
    ])
