"""Grounding check for sprint report drafts. Dependency-free so the standalone MCP
server can run it before saving."""

import re

# Facts a draft may only cite if the payload contains them: ticket refs (PAY-12, #12),
# ISO dates, and point counts ("5 pts"). Bare numbers are left alone; too many are prose.
_FACT = re.compile(
    r"\b[A-Z][A-Z0-9]*-\d+\b|#\d+\b|\b\d{4}-\d{2}-\d{2}\b|\b\d+\s*(?i:pts?|points?)\b"
)
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def ungrounded_facts(text: str, payload: dict) -> list[str]:
    """Ticket refs, dates, and point counts in text that the payload does not contain."""
    tickets = payload.get("tickets", [])
    refs = {t["ref"] for t in tickets}
    numbers_by_hash = {t["ticket_number"] for t in tickets}
    points: set[int] = set()
    dates: set[str] = set()

    def walk(value, key: str = "") -> None:
        if isinstance(value, dict):
            for item_key, item in value.items():
                walk(item, item_key)
        elif isinstance(value, list):
            for item in value:
                walk(item, key)
        elif isinstance(value, int) and not isinstance(value, bool) and "points" in key:
            points.add(value)
        elif isinstance(value, str):
            dates.update(_DATE.findall(value))

    walk(payload)
    missing = []
    for fact in _FACT.findall(text):
        if fact.startswith("#"):
            grounded = int(fact[1:]) in numbers_by_hash
        elif _DATE.fullmatch(fact):
            grounded = fact in dates
        elif fact[0].isdigit():
            grounded = int(re.match(r"\d+", fact).group()) in points
        else:
            grounded = fact in refs
        if not grounded and fact not in missing:
            missing.append(fact)
    return missing
