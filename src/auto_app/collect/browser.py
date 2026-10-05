"""Persistent, logged-in browser per source, plus passive capture of JSON responses."""

import asyncio
import json
import logging
import shutil
import subprocess
from pathlib import Path
from typing import Awaitable, Callable
from urllib.parse import urlparse

from playwright.async_api import BrowserContext, Page, Playwright, Response
from playwright.async_api import Error as PlaywrightError

from auto_app.core.browser_env import browser_env
from auto_app.core.models import CapturedResponse

log = logging.getLogger(__name__)

VIEWPORT = {"width": 1440, "height": 900}
MAX_BODY_CHARS = 3_000_000

CHROME_PATHS = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    str(Path.home() / "Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
)


def chrome_executable() -> str | None:
    for p in CHROME_PATHS:
        if Path(p).exists():
            return p
    return shutil.which("google-chrome") or shutil.which("chrome")


def open_plain_chrome(profile_dir: Path, url: str) -> subprocess.Popen:
    """Launch installed Chrome on the profile *without* automation, for logging in by hand.

    Google (and some SSO providers) refuse sign-in in automated browsers. Logging in here
    stores the site's session in the profile, which the automated session reuses later.
    """
    exe = chrome_executable()
    if not exe:
        raise FileNotFoundError("Google Chrome not found; install it from https://www.google.com/chrome/")
    profile_dir.mkdir(parents=True, exist_ok=True)
    profile_dir.chmod(0o700)
    # Playwright launches Chrome with --use-mock-keychain, so cookies must be encrypted with the
    # same mock key here; with the real Keychain key the collector can't decrypt the login cookies.
    args = [
        exe,
        f"--user-data-dir={profile_dir}",
        "--use-mock-keychain",
        "--no-first-run",
        "--no-default-browser-check",
        "--new-window",
        url,
    ]
    return subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def close_chrome(proc: subprocess.Popen, timeout: float = 15) -> None:
    """Ask Chrome to quit cleanly (SIGTERM flushes cookies to disk), then force if needed."""
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


class BrowserSession:
    """One persistent profile per source: a results tab and a detail tab."""

    def __init__(
        self,
        pw: Playwright,
        profile_dir: Path,
        *,
        headless: bool = False,
        context_hook: Callable[[BrowserContext], Awaitable[None]] | None = None,
    ):
        self.pw = pw
        self.profile_dir = profile_dir
        self.headless = headless
        self.context_hook = context_hook
        self.context: BrowserContext | None = None
        self.results: Page | None = None
        self.detail: Page | None = None

    async def __aenter__(self) -> "BrowserSession":
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self.profile_dir.chmod(0o700)
        kwargs = dict(headless=self.headless, viewport=VIEWPORT, env=browser_env())
        try:
            self.context = await self.pw.chromium.launch_persistent_context(
                str(self.profile_dir), channel="chrome", **kwargs
            )
        except PlaywrightError as e:
            log.warning("installed Chrome unavailable (%s); falling back to bundled Chromium", e.message.splitlines()[0])
            self.context = await self.pw.chromium.launch_persistent_context(str(self.profile_dir), **kwargs)
        if self.context_hook:
            await self.context_hook(self.context)
        self.results = self.context.pages[0] if self.context.pages else await self.context.new_page()
        return self

    async def detail_page(self) -> Page:
        if self.detail is None:
            self.detail = await self.context.new_page()
        return self.detail

    async def __aexit__(self, *exc) -> None:
        if self.context:
            try:
                await self.context.close()
            except PlaywrightError:
                pass


class ResponseCapture:
    """Buffers JSON responses the page receives during normal browsing. Never sends requests."""

    def __init__(self, page: Page, patterns: tuple[str, ...], site_hosts: tuple[str, ...], capture_all_json: bool):
        self.patterns = patterns
        self.site_hosts = site_hosts
        self.capture_all_json = capture_all_json
        self._generation = 0
        self._tasks: list[asyncio.Task] = []
        self._items: list[CapturedResponse] = []
        page.on("response", self._on_response)

    def reset(self) -> None:
        self._generation += 1
        self._tasks = []
        self._items = []

    async def drain(self) -> list[CapturedResponse]:
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        return list(self._items)

    def _wanted(self, resp: Response) -> bool:
        if "json" not in resp.headers.get("content-type", ""):
            return False
        if any(p in resp.url for p in self.patterns):
            return True
        if self.capture_all_json:
            host = (urlparse(resp.url).hostname or "").lower()
            return any(host == h or host.endswith("." + h) for h in self.site_hosts)
        return False

    def _on_response(self, resp: Response) -> None:
        if self._wanted(resp):
            self._tasks.append(asyncio.ensure_future(self._read(resp, self._generation)))

    async def _read(self, resp: Response, generation: int) -> None:
        try:
            text = await resp.text()
        except PlaywrightError:
            return
        truncated = len(text) > MAX_BODY_CHARS
        text = text[:MAX_BODY_CHARS]
        try:
            body = json.loads(text)
        except ValueError:
            body = text
        if generation == self._generation:
            self._items.append(
                CapturedResponse(
                    url=resp.url,
                    status=resp.status,
                    content_type=resp.headers.get("content-type", ""),
                    body=body,
                    truncated=truncated,
                )
            )
