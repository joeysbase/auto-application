"""Building the React app (web/ → web/dist, git-ignored) and running its dev server. Node is needed only here."""

import os
import shutil
import subprocess
from pathlib import Path

from auto_app.settings import PROJECT_ROOT

WEB_DIR = PROJECT_ROOT / "web"
DIST = WEB_DIR / "dist"
DEV_PORT = 5173


class BuildError(Exception):
    pass


def built(dist: Path = DIST) -> bool:
    return (dist / "index.html").is_file()


def npm() -> str:
    exe = shutil.which("npm")
    if not exe:
        raise BuildError("the web app needs Node to build (npm not found): install it (e.g. `brew install node`)")
    return exe


def build(say=print, web: Path = WEB_DIR) -> None:
    """npm install (first time, or when package.json changed) + npm run build."""
    exe = npm()
    lock, modules = web / "package-lock.json", web / "node_modules"
    if not modules.is_dir() or (lock.is_file() and lock.stat().st_mtime > modules.stat().st_mtime):
        say("installing the web app's packages (npm install)…")
        _run([exe, "install", "--no-audit", "--no-fund"], web)
    say("building the web app (npm run build)…")
    _run([exe, "run", "build"], web)


def dev_server(api_port: int, web: Path = WEB_DIR) -> subprocess.Popen:
    """Vite's dev server on DEV_PORT (hot reload), proxying /api to this server."""
    exe = npm()
    if not (web / "node_modules").is_dir():
        _run([exe, "install", "--no-audit", "--no-fund"], web)
    return subprocess.Popen([exe, "run", "dev", "--", "--port", str(DEV_PORT), "--strictPort"], cwd=web,
                            env={**os.environ, "AUTO_APP_API": f"http://127.0.0.1:{api_port}"})


def _run(cmd: list[str], cwd: Path) -> None:
    r = subprocess.run(cmd, cwd=cwd)
    if r.returncode:
        raise BuildError(f"`{' '.join(cmd[1:])}` failed in {cwd} (exit {r.returncode})")
