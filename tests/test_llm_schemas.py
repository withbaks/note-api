"""Tests for LLM output schema validation."""

import pytest
from pydantic import ValidationError

from note_ai.schemas import AISchemaOutput, ExtractResult, SuggestResult, UnderstandingResult


def test_understanding_schema_valid():
    data = UnderstandingResult.model_validate(
        {"summary": "A note about travel", "tags": ["travel"], "topics": ["trips"]}
    )
    assert data.summary == "A note about travel"


def test_understanding_schema_rejects_bad_types():
    with pytest.raises(ValidationError):
        UnderstandingResult.model_validate({"tags": "not-a-list"})


def test_suggestion_requires_kind_and_title():
    result = SuggestResult.model_validate(
        {
            "suggestions": [
                {
                    "kind": "knowledge",
                    "title": "Age",
                    "confidence": 0.9,
                }
            ]
        }
    )
    assert len(result.suggestions) == 1


def test_merged_output_roundtrip():
    merged = AISchemaOutput(
        summary="Hi",
        people=[{"display_name": "Victor"}],
        suggestions=[{"kind": "knowledge", "title": "Test"}],
        entities=[{"entity_type": "place", "value": "Lagos"}],
    )
    store = merged.to_store_dict()
    assert store["summary"] == "Hi"
    assert store["people"][0]["display_name"] == "Victor"


def test_extract_schema():
    data = ExtractResult.model_validate(
        {"people": [{"display_name": "Mum", "relationship": "mother"}], "entities": []}
    )
    assert data.people[0].display_name == "Mum"
