"""Is the system in dark mode? For the full-screen apps' `AUTO_APP_UI__THEME=auto`.

macOS: `defaults read -g AppleInterfaceStyle` prints "Dark" in dark mode (and fails in light mode).
Linux (GNOME and others following its setting): `gsettings get org.gnome.desktop.interface color-scheme`.
Windows: the `AppsUseLightTheme` registry value. None = can't tell.
"""

import subprocess
import sys


def _run(args: list[str]) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=2)
    except (OSError, subprocess.TimeoutExpired):
        return None


def system_dark() -> bool | None:
    if sys.platform == "darwin":
        r = _run(["defaults", "read", "-g", "AppleInterfaceStyle"])
        return None if r is None else "dark" in r.stdout.lower()  # light mode: the key doesn't exist
    if sys.platform.startswith("linux"):
        r = _run(["gsettings", "get", "org.gnome.desktop.interface", "color-scheme"])
        if r is None or r.returncode != 0:
            return None
        return "dark" in r.stdout.lower()
    if sys.platform == "win32":
        try:
            import winreg

            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                 r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize")
            return winreg.QueryValueEx(key, "AppsUseLightTheme")[0] == 0
        except OSError:
            return None
    return None
