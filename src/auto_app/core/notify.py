import logging
import subprocess
import sys

log = logging.getLogger(__name__)


def notify(title: str, message: str) -> None:
    """Show a macOS notification; log only on other platforms."""
    log.warning("%s: %s", title, message)
    if sys.platform != "darwin":
        return
    script = f"display notification {_quote(message)} with title {_quote(title)}"
    subprocess.run(["osascript", "-e", script], check=False, capture_output=True)


def _quote(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'
