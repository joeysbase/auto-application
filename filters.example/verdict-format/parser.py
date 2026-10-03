"""Reads this filter's own answer format: `Verdict: apply | maybe | skip` and `Reason: …`."""

import re


def parse(answer: str) -> dict:
    verdict = re.search(r"(?im)^\s*verdict:\s*(apply|maybe|skip)\b", answer)
    if not verdict:
        raise ValueError("no `Verdict:` line")
    reason = re.search(r"(?ims)^\s*reason:\s*(.+)", answer)
    return {
        "recommend": verdict.group(1).lower() in ("apply", "maybe"),  # high recall: "maybe" is shown to you
        "reason": reason.group(1).strip() if reason else "",
    }
