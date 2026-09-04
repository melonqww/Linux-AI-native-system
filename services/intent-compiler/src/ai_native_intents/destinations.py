"""Destination role resolution, independent of the model's claimed arguments."""

import re


def destination_role(
    text: str, *, has_last: bool = False, clarification: bool = False
) -> str | None:
    """Resolve one explicit, non-negated role from a natural reply."""
    if clarification:
        patterns = {
            "desktop": r"\bdesktop\b|рабоч(?:ий|ем|его)\s+стол(?:е|а)?",
            "downloads": r"\bdownloads?\b|\bзагрузк\w*",
            "documents": r"\bdocuments?\b|\bдокумент(?:ы|ах)\b",
        }
    else:
        patterns = {
            "desktop": r"\b(?:to|into|on)\s+(?:my\s+|the\s+)?desktop\b|\bна\s+рабоч(?:ий|ем)\s+стол(?:е)?",
            "downloads": r"\b(?:to|into|in)\s+(?:my\s+|the\s+)?downloads?\b|\bв\s+(?:папку\s+)?загрузк\w*",
            "documents": r"\b(?:to|into|in)\s+(?:my\s+|the\s+)?documents?\b|\bв\s+(?:папку\s+)?документ(?:ы|ах)\b",
        }
    matches = []
    for role, pattern in patterns.items():
        match = re.search(pattern, text, re.I)
        if match is None:
            continue
        clause = re.split(
            r"[,.;!?]|\bbut\b|\bа\b", text[: match.start()].casefold(), flags=re.I
        )[-1]
        nearby = re.findall(r"[\w']+", clause, re.UNICODE)[-8:]
        if any(
            word in {"не", "not", "never", "without", "dont", "don't"}
            for word in nearby
        ):
            continue
        matches.append(role)
    if len(matches) == 1:
        return matches[0]
    if not matches and has_last and re.search(r"\b(?:there|туда)\b", text, re.I):
        return "context.last_destination"
    return None
