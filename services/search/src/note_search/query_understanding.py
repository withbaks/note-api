"""Query normalization and expansion for memory search."""

from __future__ import annotations

import re

QUESTION_PREFIXES = (
    "who ",
    "what ",
    "when ",
    "where ",
    "why ",
    "how ",
    "tell me ",
    "summarize ",
    "summary of ",
    "do i ",
    "did i ",
    "can you ",
    "could you ",
    "anything i ",
    "things i ",
    "stuff i ",
)

SEARCH_STOP_WORDS = {
    "a", "an", "the", "any", "about", "note", "notes", "memory", "memories",
    "what", "where", "when", "who", "how", "is", "are", "was", "were",
    "do", "does", "did", "have", "has", "had", "i", "me", "my", "of", "in",
    "on", "to", "for", "with", "from", "and", "or", "but", "tell", "find",
    "show", "search", "ask", "please", "can", "you", "need", "remember",
    "should", "would", "could", "that", "this", "there", "here", "related",
    "anything", "something", "everything", "know", "like", "just", "really",
}

MONTH_BY_ORDINAL: dict[int, list[str]] = {
    1: ["january", "jan", "first month", "month 1", "1st month"],
    2: ["february", "feb", "second month", "month 2", "2nd month"],
    3: ["march", "mar", "third month", "month 3", "3rd month"],
    4: ["april", "apr", "fourth month", "month 4", "4th month"],
    5: ["may", "fifth month", "month 5", "5th month"],
    6: ["june", "jun", "sixth month", "month 6", "6th month"],
    7: ["july", "jul", "seventh month", "month 7", "7th month"],
    8: ["august", "aug", "eighth month", "month 8", "8th month"],
    9: ["september", "sep", "sept", "ninth month", "month 9", "9th month"],
    10: ["october", "oct", "tenth month", "month 10", "10th month"],
    11: ["november", "nov", "eleventh month", "month 11", "11th month"],
    12: ["december", "dec", "twelfth month", "month 12", "12th month"],
}

ORDINAL_WORDS = {
    "first": 1,
    "second": 2,
    "third": 3,
    "fourth": 4,
    "fifth": 5,
    "sixth": 6,
    "seventh": 7,
    "eighth": 8,
    "ninth": 9,
    "tenth": 10,
    "eleventh": 11,
    "twelfth": 12,
}


def looks_like_question(query: str) -> bool:
    q = query.strip().lower()
    if not q or len(q) < 4:
        return False
    if q.endswith("?"):
        return True
    if any(q.startswith(p) for p in QUESTION_PREFIXES):
        return True
    return bool(
        re.search(
            r"\b(what|when|where|who|how|anything|something|remember)\b.*\b(about|on|in|for|from|during)\b",
            q,
        )
    )


def extract_search_terms(query: str) -> list[str]:
    words = re.findall(r"[a-z0-9]+", query.lower())
    return [w for w in words if len(w) > 1 and w not in SEARCH_STOP_WORDS]


def expand_search_queries(query: str) -> list[str]:
    """Return the original query plus heuristic expansions for dates, ordinals, etc."""
    base = query.strip()
    if not base:
        return []

    expanded: list[str] = [base]
    lower = base.lower()

    ordinal_month = re.search(r"\b(\d+)(?:st|nd|rd|th)\s+month\b", lower)
    if ordinal_month:
        month_num = int(ordinal_month.group(1))
        expanded.extend(MONTH_BY_ORDINAL.get(month_num, []))

    word_month = re.search(r"\b(first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|eleventh|twelfth)\s+month\b", lower)
    if word_month:
        month_num = ORDINAL_WORDS[word_month.group(1)]
        expanded.extend(MONTH_BY_ORDINAL.get(month_num, []))

    if re.search(r"\b7th\b|\bseventh\b", lower) and "month" in lower:
        expanded.extend(MONTH_BY_ORDINAL[7])

    if re.search(r"\bpregnan", lower):
        expanded.extend(["due date", "trimester", "baby", "prenatal", "week", "month"])

    if re.search(r"\b(birthday|born|turns?\s+\d+)", lower):
        expanded.extend(["birthday", "age", "born", "date of birth"])

    if re.search(r"\b(trip|travel|flight|hotel|vacation)\b", lower):
        expanded.extend(["itinerary", "booking", "reservation", "departure", "arrival"])

    # Dedupe while preserving order.
    seen: set[str] = set()
    ordered: list[str] = []
    for item in expanded:
        key = item.lower()
        if key in seen:
            continue
        seen.add(key)
        ordered.append(item)
    return ordered
