"""Build privacy-minimized LLM prompts from memory text and heuristics."""

from __future__ import annotations

import re
from typing import Any

from note_ai.heuristics import HeuristicHelper

_PII_KEYS = frozenset(
    {
        "is_phone_number",
        "is_mothers_phone_no",
        "is_age",
        "is_monthly_budget",
        "is_shoe_size",
    }
)


def build_llm_payload(
    text: str, heuristic_helpers: list[HeuristicHelper]
) -> tuple[str, list[dict[str, Any]], dict[str, str]]:
    """Return (redacted_text, heuristic_json, placeholder_map)."""
    redacted = text
    placeholder_map: dict[str, str] = {}
    counter = 0

    for helper in heuristic_helpers:
        if helper.key not in _PII_KEYS or not helper.value:
            continue
        token = f"[{helper.key.upper()}_{counter}]"
        counter += 1
        placeholder_map[token] = helper.value
        redacted = re.sub(re.escape(helper.value), token, redacted, count=1)

    # Strip media URIs from prompts
    redacted = re.sub(r"https?://\S+", "[MEDIA_URI]", redacted)
    redacted = re.sub(r"file://\S+", "[LOCAL_URI]", redacted)

    heuristic_json = [
        {
            "key": h.key,
            "value": h.value if h.key not in _PII_KEYS else "[redacted]",
            "subject": h.subject,
            "display_name": h.display_name,
            "relationship": h.relationship,
            "semantic_type": h.semantic_type,
        }
        for h in heuristic_helpers
    ]
    return redacted, heuristic_json, placeholder_map
