"""Detect login walls, CAPTCHAs and blocks. Hard signals stop the source; never work around them."""

import re

from playwright.async_api import Page, Response

from auto_app.sources.base import SourceAdapter
from auto_app.sources.common import js

HARD_STATUSES = {403, 429, 999}
GONE_STATUSES = {404, 410}
MAX_SOFT_STREAK = 3
CHALLENGE_URL = re.compile(r"/(cdn-cgi/challenge|[^?]*security/challenge|[^?]*captcha)", re.IGNORECASE)
# This machine has no network (Wi-Fi down, asleep, DNS gone): nothing the site did.
OFFLINE_ERRORS = re.compile(r"net::ERR_(INTERNET_DISCONNECTED|NAME_NOT_RESOLVED|NETWORK_CHANGED|ADDRESS_UNREACHABLE"
                            r"|NETWORK_IO_SUSPENDED)\b")


class HardBlock(Exception):
    """Stop the source now and put it in cooldown."""


class NeedsLogin(HardBlock):
    """Session expired or login wall; user must run `auto-app source login`."""


class ChallengeDetected(HardBlock):
    """A security check (CAPTCHA / Cloudflare interstitial). A person may complete it by hand."""


class NetworkDown(HardBlock):
    """No network on this machine: stop the source for this run, but no cooldown; the next run tries again."""


class SoftFailure(Exception):
    def __init__(self, message: str, retryable: bool = True):
        super().__init__(message)
        self.retryable = retryable


class HealthGuard:
    def __init__(self, adapter: SourceAdapter):
        self.adapter = adapter
        self.soft_streak = 0

    async def check(self, page: Page, response: Response | None) -> None:
        status = response.status if response else None
        if self.adapter.is_login_url(page.url):
            raise NeedsLogin(f"redirected to login: {page.url}")
        # Challenges often come with a 403, so look for them before judging the status.
        if challenge := await self.detect_challenge(page):
            raise ChallengeDetected(challenge)
        if status in HARD_STATUSES:
            raise HardBlock(f"HTTP {status} at {page.url}")
        if status in GONE_STATUSES:
            raise SoftFailure(f"HTTP {status}", retryable=False)
        if status and status >= 500:
            raise SoftFailure(f"HTTP {status}")

    async def detect_challenge(self, page: Page) -> str | None:
        if CHALLENGE_URL.search(page.url):
            return f"security check page: {page.url}"
        found = await page.evaluate(js.DETECT_CAPTCHA)
        return f"{found} at {page.url}" if found else None

    def record_success(self) -> None:
        self.soft_streak = 0

    def record_soft_failure(self, err: Exception) -> None:
        self.soft_streak += 1
        if self.soft_streak >= MAX_SOFT_STREAK:
            raise HardBlock(f"{self.soft_streak} consecutive failures, last: {err}")
