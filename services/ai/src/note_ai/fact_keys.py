"""Canonical knowledge fact keys and action helper keys."""

from __future__ import annotations

import re

# Closed set for LLM knowledge suggestions (+ preference:* and custom:<slug>)
KNOWLEDGE_FACT_KEYS = frozenset(
    {
        "full_name",
        "nickname",
        "birthday",
        "age",
        "phone_number",
        "email",
        "address",
        "shoe_size",
        "monthly_budget",
        "employer",
        "job_title",
        "school",
        "hometown",
        "favorite_food",
        "favorite_restaurant",
        "allergy",
        "pet_name",
        "relationship_status",
        "identity",
        "person_meta",
        "contact",
        "preference",
    }
)

ACTION_HELPER_KEYS = frozenset(
    {
        "is_reminder",
        "is_calendar",
        "is_birthday",
        "link_person",
        "assign_category",
        "open_thread",
        "confirm_thread",
    }
)

ACTION_TOOLS = frozenset(
    {
        "create_reminder",
        "create_calendar_event",
        "link_person",
        "assign_category",
        "open_thread",
        "confirm_thread",
    }
)

_CUSTOM_KEY_RE = re.compile(r"^custom:[a-z0-9_]{1,64}$")
_PREF_KEY_RE = re.compile(r"^preference:[a-z0-9_]{1,64}$")


def is_valid_knowledge_fact_key(key: str | None) -> bool:
    if not key or not isinstance(key, str):
        return False
    k = key.strip().lower()
    if k in KNOWLEDGE_FACT_KEYS:
        return True
    if _CUSTOM_KEY_RE.match(k) or _PREF_KEY_RE.match(k):
        return True
    return False


def normalize_knowledge_fact_key(key: str | None, *, semantic_type: str | None = None) -> str | None:
    """Return a valid fact key, mapping freeform into custom:<slug> when possible."""
    if not key:
        return None
    k = key.strip().lower().replace(" ", "_").replace("-", "_")
    if is_valid_knowledge_fact_key(k):
        return k
    # legacy helper keys like is_shoe_size
    if k.startswith("is_"):
        rest = k[3:]
        if is_valid_knowledge_fact_key(rest):
            return rest
    slug = re.sub(r"[^a-z0-9_]", "", k)[:64]
    if not slug:
        if semantic_type and is_valid_knowledge_fact_key(semantic_type):
            return semantic_type
        return None
    custom = f"custom:{slug}"
    return custom if is_valid_knowledge_fact_key(custom) else None


def is_action_suggestion(key: str | None, kind: str | None, tool: str | None = None) -> bool:
    if kind in ("system", "action"):
        return True
    if key and key in ACTION_HELPER_KEYS:
        return True
    if tool and tool in ACTION_TOOLS:
        return True
    return False
