"""Destination role resolution, independent of the model's claimed arguments."""

import re


def destination_role(text: str, *, has_last: bool = False) -> str | None:
    patterns = {
        "desktop": r"\bdesktop\b|рабоч(?:ий|ем|его)\s+стол",
        "downloads": r"\bdownloads\b|\bзагрузк",
        "documents": r"\b(?:to|in|into)\s+(?:my\s+)?documents\b|в\s+(?:папку\s+)?[«\"]?документы",
    }
    matches = [
        role for role, pattern in patterns.items() if re.search(pattern, text, re.I)
    ]
    if len(matches) == 1:
        return matches[0]
    if not matches and has_last and re.search(r"\b(?:there|туда)\b", text, re.I):
        return "context.last_destination"
    return None
