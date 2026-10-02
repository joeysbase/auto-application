"""Card-level filters: explicit, cheap rules applied before any detail fetch."""

import re

from auto_app.core.config import CardFilters
from auto_app.core.models import Card


def reject_reason(card: Card, filters: CardFilters) -> str | None:
    title = card.title or (card.lines[0] if card.lines else "")
    for word in filters.exclude_title_words:
        if re.search(rf"\b{re.escape(word)}\b", title, re.IGNORECASE):
            return f"title word: {word}"
    lines = [line.lower() for line in card.lines]
    for company in filters.exclude_companies:
        c = company.lower()
        if any(line == c or line.startswith(c + " ") for line in lines):
            return f"company: {company}"
    return None
