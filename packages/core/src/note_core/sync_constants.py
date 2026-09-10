"""Shared sync and AI confidence constants."""

from uuid import UUID

SYSTEM_DEVICE_ID = UUID("00000000-0000-0000-0000-000000000001")

HIGH_CONFIDENCE_THRESHOLD = 0.85
REVIEW_CONFIDENCE_THRESHOLD = 0.50

# Per-helper-kind thresholds (tuned via eval/helpers_golden.jsonl)
CONFIDENCE_BY_KIND: dict[str, float] = {
    "is_phone_number": 0.95,
    "is_mothers_phone_no": 0.95,
    "is_age": 0.90,
    "is_shoe_size": 0.90,
    "is_monthly_budget": 0.88,
    "is_birthday": 0.85,
    "is_reminder": 0.80,
    "is_calendar": 0.80,
}

CONFIDENCE_BY_SEMANTIC: dict[str, float] = {
    "contact": 0.95,
    "measurement": 0.90,
    "person_meta": 0.85,
    "preference": 0.80,
    "reminder": 0.80,
    "connection": 0.70,
}


def confidence_threshold_for(helper_key: str | None, semantic_type: str | None) -> float:
    if helper_key and helper_key in CONFIDENCE_BY_KIND:
        return CONFIDENCE_BY_KIND[helper_key]
    if semantic_type and semantic_type in CONFIDENCE_BY_SEMANTIC:
        return CONFIDENCE_BY_SEMANTIC[semantic_type]
    return HIGH_CONFIDENCE_THRESHOLD
