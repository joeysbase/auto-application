"""The browser for the automated Simplify applier (docs/step3-apply.md §3): Playwright's own Chromium with the
Simplify Copilot extension loaded and a dedicated profile — `browser-profiles/simplify/` (git-ignored), where you log
in to Simplify once (`auto-app apply login simplify`).

Why not your Chrome: branded Chrome (137+) no longer loads extensions under automation, so the extension is copied
from your Chrome profile (the newest installed version) into `browser-profiles/simplify-extension/` and loaded into
Chromium. When Chrome updates the extension, the next launch copies the new version.
"""

import shutil
from pathlib import Path

EXTENSION_ID = "pbanhockgagggenencehbnadejlgchfc"  # Simplify Copilot (Chrome Web Store)
CHROME_PROFILES = Path.home() / "Library" / "Application Support" / "Google" / "Chrome"
LOGIN_URL = "https://simplify.jobs/auth/login"


def _version_key(name: str):
    return [int(x) if x.isdigit() else x for x in name.replace("_", ".").split(".")]


def installed_extension(chrome_dir: Path = CHROME_PROFILES) -> Path | None:
    """The newest installed Simplify Copilot in any of your Chrome profiles (its unpacked folder), or None."""
    found = []
    for ext in chrome_dir.glob(f"*/Extensions/{EXTENSION_ID}/*"):
        if (ext / "manifest.json").is_file():
            found.append(ext)
    return max(found, key=lambda p: _version_key(p.name)) if found else None


def prepare_extension(dest: Path, chrome_dir: Path = CHROME_PROFILES) -> Path:
    """Copy the installed extension to `dest` (refreshed when Chrome has a newer version). Returns the copy."""
    src = installed_extension(chrome_dir)
    if src is None:
        raise RuntimeError("Simplify Copilot isn't installed in Chrome — install it from the Chrome Web Store first")
    stamp = dest / ".source-version"
    if not (dest.is_dir() and stamp.is_file() and stamp.read_text() == src.name):
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(src, dest, ignore=shutil.ignore_patterns("_metadata"))  # Web Store signatures: not needed unpacked
        stamp.write_text(src.name)
    return dest


def profile_in_use(profile: Path) -> int | None:
    """The pid of a running Chromium that holds this profile (its SingletonLock → "host-pid"), or None. A second
    launch on a profile in use just hands its URL to the running one and exits."""
    import os

    lock = profile / "SingletonLock"
    try:
        target = os.readlink(lock)
    except OSError:
        return None
    pid = target.rsplit("-", 1)[-1]
    if not pid.isdigit():
        return None
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return None  # a stale lock: Chromium recovers by itself
    except PermissionError:
        pass
    return int(pid)


QUIET = {"ignore_default_args": ["--enable-automation"],  # no "controlled by automated software" bar
         "args": ["--disable-blink-features=AutomationControlled"]}  # navigator.webdriver stays false


def launch(pw, profiles: Path, headless: bool = False, quiet: bool = False):
    """A persistent Chromium context with Simplify loaded; profiles = the project's browser-profiles/ folder.
    quiet: hide the automation markers sites check (used for logging in — Google refuses sign-ins in a browser it can
    tell is automated)."""
    ext = prepare_extension(profiles / "simplify-extension")
    args = [f"--disable-extensions-except={ext}", f"--load-extension={ext}"]
    if headless:
        args.append("--headless=new")  # the new headless mode runs extensions; the old one doesn't
    extra = {}
    if quiet:
        args += QUIET["args"]
        extra["ignore_default_args"] = QUIET["ignore_default_args"]
    from auto_app.core.browser_env import browser_env

    return pw.chromium.launch_persistent_context(str(profiles / "simplify"), headless=False, args=args,
                                                 env=browser_env(), **extra)  # no DYLD_*: see core/browser_env.py
