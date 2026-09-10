"""Schema-scoped LLM prompts for the memory pipeline."""

from __future__ import annotations

import json
from typing import Any


def understand_prompt(text: str, heuristic_json: list[dict[str, Any]]) -> str:
    return f"""Analyze this user memory and return JSON only with understanding fields.

{{
  "summary": "brief summary",
  "tags": ["tag1"],
  "topics": ["topic1"],
  "connections": [],
  "why_it_matters": "why this might matter",
  "keyword_summary": "comma separated keywords",
  "temporal_refs": ["July 2025"],
  "people_refs": ["Joshua"],
  "place_refs": ["Lagos"],
  "intent_tags": ["reminder", "travel"]
}}

Heuristic pre-analysis (may contain redacted values):
{json.dumps(heuristic_json)}

Memory:
{text}"""


def extract_prompt(text: str, heuristic_json: list[dict[str, Any]]) -> str:
    return f"""Extract people and entities from this memory. Return JSON only.

{{
  "people": [{{"display_name": "Victor", "relationship": "friend", "role": "mentioned"}}],
  "entities": [{{"entity_type": "place", "value": "Lagos"}}]
}}

Heuristic pre-analysis:
{json.dumps(heuristic_json)}

Memory:
{text}"""


def suggest_prompt(text: str, heuristic_json: list[dict[str, Any]]) -> str:
    return f"""Propose knowledge facts and actionable helpers for this memory. Return JSON only.

kind "knowledge": durable facts written by the agent (user does NOT tap Save).
  proposed_fact_key MUST be one of:
    full_name, nickname, birthday, age, phone_number, email, address, shoe_size,
    monthly_budget, employer, job_title, school, hometown, favorite_food,
    favorite_restaurant, allergy, pet_name, relationship_status, identity,
    person_meta, contact, preference,
    preference:<slug>, custom:<slug>
  Never invent freeform fact keys outside that set.

kind "action": actionable chips only — reminders, calendar, link person, assign category.
  Use tool: create_reminder | create_calendar_event | link_person | assign_category
  Include metadata as needed (scheduled_at, category_name, person_ref).

semantic_type: person_meta, contact, measurement, health, education, event, place, product, preference, reminder, goal.
person_ref: "self" or a display name / relationship.
fact_type (knowledge only): attribute, preference, template, contact_field.

{{
  "suggestions": [{{
    "kind": "knowledge",
    "title": "Victor's pet",
    "description": null,
    "value": "Max",
    "person_ref": "Victor",
    "proposed_fact_key": "pet_name",
    "semantic_type": "person_meta",
    "fact_type": "attribute",
    "span_text": "Max",
    "confidence": 0.9
  }}, {{
    "kind": "action",
    "title": "Reminder",
    "description": "Remind you to call Mum tomorrow at 9am?",
    "value": "call Mum",
    "person_ref": "self",
    "semantic_type": "reminder",
    "tool": "create_reminder",
    "key": "is_reminder",
    "metadata": {{"scheduled_at": "2026-01-01T09:00:00Z"}},
    "confidence": 0.85
  }}],
  "suggested_categories": ["Family"]
}}

Knowledge is auto-stored; only emit action chips for user interaction.
Drop wrong heuristics.

Heuristic pre-analysis:
{json.dumps(heuristic_json)}

Memory:
{text}"""


def validation_retry_suffix(errors: str) -> str:
    return f"""

Your previous JSON failed validation. Fix these errors and return valid JSON only:
{errors}"""
