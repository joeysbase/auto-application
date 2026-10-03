"""Detect which job platform a company's careers page uses (for `auto-app source add`)."""

import re

import httpx

USER_AGENT = "Mozilla/5.0 (compatible; auto-app/0.1; personal job search)"

PATTERNS: list[tuple[str, re.Pattern]] = [
    ("ashby", re.compile(r"(?:jobs|api)\.ashbyhq\.com/(?:posting-api/job-board/)?([A-Za-z0-9._-]+)")),
    ("greenhouse", re.compile(r"greenhouse\.io/embed/job_board(?:/js)?\?for=([A-Za-z0-9_-]+)")),
    ("greenhouse", re.compile(r"(?:boards|job-boards)\.greenhouse\.io/([A-Za-z0-9_-]+)")),
    ("greenhouse", re.compile(r"boards-api\.greenhouse\.io/v1/boards/([A-Za-z0-9_-]+)")),
    ("lever", re.compile(r"jobs\.(?:eu\.)?lever\.co/([A-Za-z0-9._-]+)")),
    ("workday", re.compile(r"(https?://[a-z0-9-]+\.wd\d+\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?[A-Za-z0-9_-]+)")),
]
NOT_BOARDS = {"embed", "v1", "api", "posting-api", "jobs", "js", "static", "assets", "wday"}


def find_platforms(text: str) -> list[tuple[str, str]]:
    """(platform, board) candidates in a URL or page, most frequent first."""
    counts: dict[tuple[str, str], int] = {}
    for platform, pat in PATTERNS:
        for m in pat.finditer(text):
            board = m.group(1).rstrip("/.")
            if board.lower() in NOT_BOARDS:
                continue
            if platform == "workday" and board.rsplit("/", 1)[-1] in NOT_BOARDS:
                continue
            counts[(platform, board)] = counts.get((platform, board), 0) + 1
    return sorted(counts, key=lambda k: -counts[k])


async def detect(url: str) -> tuple[list[tuple[str, str]], str]:
    """Platforms found in the URL itself, else in the page it serves. Also returns the final URL."""
    direct = find_platforms(url)
    if direct:
        return direct, url
    async with httpx.AsyncClient(timeout=30, follow_redirects=True, headers={"User-Agent": USER_AGENT}) as client:
        r = await client.get(url)
        return find_platforms(str(r.url) + "\n" + r.text), str(r.url)
