"""Regex/heuristic pre-processing before LLM confirmation."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime


@dataclass
class HeuristicHelper:
    key: str
    value: str | None
    person: str | None = None  # legacy display string
    subject: str = "self"  # "self" | "named"
    display_name: str | None = None
    relationship: str | None = None
    semantic_type: str | None = None
    scheduled_at: datetime | None = None


PHONE_RE = re.compile(r"\b(?:\+?\d{1,3}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4,6}\b")

# Joshua's shoe size is 12 / my shoe size is 10 / mum wears size 8
SHOE_SIZE_RE = re.compile(
    r"\b(?:(?P<possessor>\w+)'s\s+)?(?:my\s+)?shoe\s+sizes?\s*(?:is|are|:)?\s*(?P<size>\d+(?:\.\d+)?)\b|"
    r"\b(?:(?P<wearer>my|mum|mom|mother|dad|father|brother|sister|\w+)\s+)?"
    r"(?:wears?|wear)\s+(?:size\s+)?(?P<size2>\d+(?:\.\d+)?)\b",
    re.IGNORECASE,
)

# Require years-old for "is N"; allow turned N / age is N
AGE_RE = re.compile(
    r"\b(?:(?:my\s+)?(?P<person>mum|mom|mother|dad|father|brother|sister|grandma|grandpa|[A-Z][a-z]+)\s+)?"
    r"(?:is|turned)\s+(?P<age>\d{1,3})\s*(?:years?\s*old|y\.?o\.?)\b|"
    r"\b(?:(?:my\s+)?(?P<person2>mum|mom|mother|dad|father|brother|sister|grandma|grandpa|[A-Z][a-z]+)\s+)?"
    r"(?:age(?:\s+is)?|turned)\s+(?P<age2>\d{1,3})\b",
    re.IGNORECASE,
)

SIZE_CONTEXT_RE = re.compile(
    r"\b(?:shoe|boot|dress|pant|waist|shirt)\s*sizes?\b|\bsize\s*(?:is|are|:)?\s*\d",
    re.IGNORECASE,
)

MY_PHONE_RE = re.compile(
    r"\b(?:my\s+(?:phone\s+)?(?:number|no\.?)|number\s+is)\s*[:\s]*(?P<phone>"
    r"(?:\+?\d{1,3}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4,6})",
    re.IGNORECASE,
)

REMINDER_RE = re.compile(
    r"(?:remind\s+me|don't\s+forget|remember\s+to)\s+(.+?)(?:\.|$)",
    re.IGNORECASE,
)
BIRTHDAY_RE = re.compile(
    r"(?:(?:(?P<person>\w+)(?:'s)?\s+birthday\s+(?:is\s+)?(?:on\s+)?)|"
    r"(?:(?P<my>my)\s+birthday\s+(?:is\s+)?(?:on\s+)?)|"
    r"(?:(?P<date_first>\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?|"
    r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*\s+\d{1,2})"
    r"\s+is\s+my\s+birthday))",
    re.IGNORECASE,
)
BUDGET_RE = re.compile(
    r"(?:monthly\s+budget|budget)[:\s]+(?:\$|£|€|₦)?\s*([\d,]+(?:\.\d{2})?)",
    re.IGNORECASE,
)
DATE_RE = re.compile(
    r"\b(?:on\s+)?(\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?|"
    r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*\s+\d{1,2}(?:,?\s+\d{4})?)\b",
    re.IGNORECASE,
)

RELATION_MAP = {
    "mum": "mother",
    "mom": "mother",
    "mother": "mother",
    "dad": "father",
    "father": "father",
    "brother": "brother",
    "sister": "sister",
    "grandma": "grandma",
    "grandpa": "grandpa",
}


def _subject_from_token(token: str | None) -> tuple[str, str | None, str | None]:
    """Return (subject, display_name, relationship)."""
    if not token:
        return "self", None, "self"
    t = token.strip().lower()
    if t in {"my", "me", "i", "myself"}:
        return "self", None, "self"
    if t in RELATION_MAP:
        rel = RELATION_MAP[t]
        display = {"mother": "Mum", "father": "Dad"}.get(rel, t.capitalize())
        return "named", display, rel
    # Proper name
    display = token.strip()
    display = display[0].upper() + display[1:] if display else token
    return "named", display, None


def run_heuristics(text: str) -> tuple[list[HeuristicHelper], dict]:
    helpers: list[HeuristicHelper] = []
    entities: list[dict] = []

    shoe_match = SHOE_SIZE_RE.search(text)
    if shoe_match:
        size = shoe_match.group("size") or shoe_match.group("size2")
        token = shoe_match.group("possessor") or shoe_match.group("wearer")
        # "my shoe size" → self; "Joshua's" → named
        if re.search(r"\bmy\s+shoe\s+size", text, re.IGNORECASE) and not shoe_match.group("possessor"):
            subject, display, rel = "self", None, "self"
        else:
            subject, display, rel = _subject_from_token(token)
        helpers.append(
            HeuristicHelper(
                key="is_shoe_size",
                value=size,
                person=display.lower() if display else None,
                subject=subject,
                display_name=display,
                relationship=rel,
                semantic_type="measurement",
            )
        )
        entities.append({"entity_type": "shoe_size", "value": size, "person": display})

    if not shoe_match and not SIZE_CONTEXT_RE.search(text):
        age_match = AGE_RE.search(text)
        if age_match:
            token = age_match.group("person") or age_match.group("person2")
            subject, display, rel = _subject_from_token(token)
            # "my mum is 78" — person group is mum
            age = age_match.group("age") or age_match.group("age2")
            helpers.append(
                HeuristicHelper(
                    key="is_age",
                    value=age,
                    person=display.lower() if display else None,
                    subject=subject,
                    display_name=display,
                    relationship=rel,
                    semantic_type="person_meta",
                )
            )
            entities.append({"entity_type": "age", "value": age, "person": display})

    phone_labeled = MY_PHONE_RE.search(text)
    if phone_labeled:
        phone = phone_labeled.group("phone")
        helpers.append(
            HeuristicHelper(
                key="is_phone_number",
                value=phone,
                subject="self",
                relationship="self",
                semantic_type="contact",
            )
        )
        entities.append({"entity_type": "phone", "value": phone})
    else:
        for match in PHONE_RE.finditer(text):
            phone = match.group(0)
            helpers.append(
                HeuristicHelper(
                    key="is_phone_number",
                    value=phone,
                    subject="self",
                    relationship="self",
                    semantic_type="contact",
                )
            )
            entities.append({"entity_type": "phone", "value": phone})

    reminder_match = REMINDER_RE.search(text)
    if reminder_match:
        helpers.append(
            HeuristicHelper(
                key="is_reminder",
                value=reminder_match.group(1).strip(),
                subject="self",
                relationship="self",
                semantic_type="reminder",
            )
        )

    birthday_match = BIRTHDAY_RE.search(text)
    if birthday_match:
        if birthday_match.group("my") or birthday_match.group("date_first"):
            subject, display, rel = "self", None, "self"
            date_str = birthday_match.group("date_first")
            if not date_str:
                # "my birthday is DATE" — re-extract date
                d = DATE_RE.search(text)
                date_str = d.group(0) if d else None
        else:
            subject, display, rel = _subject_from_token(birthday_match.group("person"))
            date_str = None
            d = DATE_RE.search(text)
            if d:
                date_str = d.group(0)
        helpers.append(
            HeuristicHelper(
                key="is_birthday",
                value=date_str,
                person=display.lower() if display else None,
                subject=subject,
                display_name=display,
                relationship=rel,
                semantic_type="event",
            )
        )

    budget_match = BUDGET_RE.search(text)
    if budget_match:
        helpers.append(
            HeuristicHelper(
                key="is_monthly_budget",
                value=budget_match.group(1),
                subject="self",
                relationship="self",
                semantic_type="preference",
            )
        )

    if DATE_RE.search(text) and not any(h.key == "is_birthday" for h in helpers):
        date_val = DATE_RE.search(text).group(0)
        helpers.append(
            HeuristicHelper(
                key="is_calendar",
                value=date_val,
                subject="self",
                relationship="self",
                semantic_type="event",
            )
        )

    metadata = {
        "has_phone": any(h.key == "is_phone_number" for h in helpers),
        "has_reminder": any(h.key == "is_reminder" for h in helpers),
        "has_date": any(h.key in ("is_calendar", "is_birthday") for h in helpers),
        "has_age": any(h.key == "is_age" for h in helpers),
        "has_shoe_size": any(h.key == "is_shoe_size" for h in helpers),
    }
    return helpers, {"entities": entities, "metadata": metadata}
