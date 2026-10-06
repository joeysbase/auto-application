"""Durations as you'd write them: 30m, 1h30m, 90s, 2h (a bare number: minutes)."""

import re

UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}
PART = re.compile(r"(\d+(?:\.\d+)?)\s*([smhd])", re.I)


def parse_duration(text: str | int | float) -> float:
    """Seconds."""
    if isinstance(text, (int, float)):
        return float(text) * 60
    s = str(text).strip().lower()
    if re.fullmatch(r"\d+(\.\d+)?", s):
        return float(s) * 60
    parts = PART.findall(s)
    if not parts or PART.sub("", s).strip():
        raise ValueError(f"not a duration: {text!r} (e.g. 30m, 1h30m, 90s)")
    return sum(float(n) * UNITS[u.lower()] for n, u in parts)


def format_duration(seconds: float) -> str:
    seconds = int(round(seconds))
    h, rest = divmod(seconds, 3600)
    m, s = divmod(rest, 60)
    if h:
        return f"{h}h{m:02d}m" if m else f"{h}h"
    if m:
        return f"{m} min" if not s else f"{m}m{s:02d}s"
    return f"{s} s"
