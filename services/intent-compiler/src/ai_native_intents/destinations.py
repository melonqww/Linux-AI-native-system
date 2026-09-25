"""Destination role resolution, independent of the model's claimed arguments."""

import re


_CLARIFICATION_ALIASES = {
    "desktop": (
        ("desktop",),
        ("рабочий", "стол"),
        ("рабочем", "столе"),
        ("рабочего", "стола"),
    ),
    "downloads": (
        ("download",),
        ("downloads",),
        ("загрузки",),
        ("загрузках",),
        ("загрузку",),
    ),
    "documents": (
        ("document",),
        ("documents",),
        ("документы",),
        ("документах",),
    ),
}


def _one_edit_apart(left: str, right: str) -> bool:
    """Return true for one substitution, insertion, deletion or transposition."""
    if left == right or min(len(left), len(right)) < 4:
        return False
    if len(left) == len(right):
        differences = [
            index
            for index, pair in enumerate(zip(left, right))
            if pair[0] != pair[1]
        ]
        if len(differences) == 1:
            return True
        return (
            len(differences) == 2
            and differences[1] == differences[0] + 1
            and left[differences[0]] == right[differences[1]]
            and left[differences[1]] == right[differences[0]]
        )
    if abs(len(left) - len(right)) != 1:
        return False
    shorter, longer = (left, right) if len(left) < len(right) else (right, left)
    short_index = long_index = edits = 0
    while short_index < len(shorter) and long_index < len(longer):
        if shorter[short_index] == longer[long_index]:
            short_index += 1
            long_index += 1
            continue
        edits += 1
        long_index += 1
        if edits > 1:
            return False
    edits += len(longer) - long_index
    return edits == 1


def _adjacent_transposition_apart(left: str, right: str) -> bool:
    if len(left) != len(right):
        return False
    differences = [
        index for index, pair in enumerate(zip(left, right)) if pair[0] != pair[1]
    ]
    return (
        len(differences) == 2
        and differences[1] == differences[0] + 1
        and left[differences[0]] == right[differences[1]]
        and left[differences[1]] == right[differences[0]]
    )


def _fuzzy_clarification_mentions(text: str) -> list[tuple[str, int]]:
    """Find one-typo matches for the closed destination vocabulary."""
    tokens = [
        (match.group().casefold(), match.start())
        for match in re.finditer(r"[^\W_]+", text, re.UNICODE)
    ]
    mentions: set[tuple[str, int]] = set()
    for role, aliases in _CLARIFICATION_ALIASES.items():
        for alias in aliases:
            for index in range(len(tokens) - len(alias) + 1):
                window = tokens[index : index + len(alias)]
                typo_count = 0
                for (word, _), expected in zip(window, alias):
                    if word == expected:
                        continue
                    if not _one_edit_apart(word, expected):
                        break
                    typo_count += 1
                else:
                    if typo_count == 1:
                        mentions.add((role, window[0][1]))
    return sorted(mentions, key=lambda item: item[1])


def _is_negated(text: str, start: int) -> bool:
    clause = re.split(
        r"[,.:;!?]|\bbut\b|\bа\b", text[:start].casefold(), flags=re.I
    )[-1]
    nearby = re.findall(r"[\w']+", clause, re.UNICODE)[-8:]
    negators = {"не", "no", "not", "never", "without", "dont", "don't"}
    return any(
        word in negators
        or any(
            len(negator) >= 3 and _adjacent_transposition_apart(word, negator)
            for negator in negators
        )
        for word in nearby
    )


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
    mentions: list[tuple[str, int]] = []
    for role, pattern in patterns.items():
        for match in re.finditer(pattern, text, re.I):
            mentions.append((role, match.start()))
    if clarification:
        mentions.extend(_fuzzy_clarification_mentions(text))
    matches = {
        role for role, start in mentions if not _is_negated(text, start)
    }
    if len(matches) == 1:
        return next(iter(matches))
    if not matches and has_last:
        previous = re.search(r"\b(?:there|туда)\b", text, re.I)
        if previous is not None and not _is_negated(text, previous.start()):
            return "context.last_destination"
    return None


def explicitly_named_destination(value: str, text: str) -> bool:
    """Corroborate a proposed folder name before removing it from search text.

    A second occurrence may independently be a content topic, so that case is
    deliberately left to clarification instead of silently dropping a filter.
    """
    if not value.strip() or len(value) > 120:
        return False
    name = re.escape(value.strip())
    occurrences = list(re.finditer(rf"(?<!\w){name}(?!\w)", text, re.I))
    if len(occurrences) != 1:
        return False
    patterns = (
        rf"\b(?:folder|directory)\s+(?:called|named)\s+['\"«]?{name}(?!\w)",
        rf"\bпапк\w*\s+(?:(?:с\s+именем|под\s+названием)\s+)?['\"«]?{name}(?!\w)",
    )
    return any(re.search(pattern, text, re.I) for pattern in patterns)
