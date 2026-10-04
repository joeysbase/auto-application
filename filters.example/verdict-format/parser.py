"""Reads this filter's own answer format: `Verdict: apply | maybe | skip`, `Dealbreaker: yes | no`, `Reason: …`."""

import re


def parse(answer: str) -> dict:
    verdict = re.search(r"(?im)^\s*verdict:\s*(apply|maybe|skip)\b", answer)
    if not verdict:
        raise ValueError("no `Verdict:` line")
    breaker = re.search(r"(?im)^\s*dealbreaker:\s*(yes|no)\b", answer)
    reason = re.search(r"(?ims)^\s*reason:\s*(.+)", answer)
    return {
        "fit": verdict.group(1).lower() in ("apply", "maybe"),  # high recall: "maybe" is shown to you
        "dealbreaker": breaker.group(1).lower() == "yes" if breaker else None,  # missing: not given
        "reason": reason.group(1).strip() if reason else "",
    }
