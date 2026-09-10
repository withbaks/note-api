"""Pydantic schemas for LLM pipeline output."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from note_ai.fact_keys import (
    normalize_knowledge_fact_key,
)


class UnderstandingResult(BaseModel):
    summary: str | None = None
    tags: list[str] | None = None
    topics: list[str] | None = None
    connections: list[str] | None = None
    why_it_matters: str | None = None
    keyword_summary: str | None = None
    temporal_refs: list[str] | None = None
    people_refs: list[str] | None = None
    place_refs: list[str] | None = None
    intent_tags: list[str] | None = None


class PersonResult(BaseModel):
    display_name: str
    relationship: str | None = None
    role: str | None = "mentioned"


class EntityResult(BaseModel):
    entity_type: str
    value: str


class SuggestionResult(BaseModel):
    kind: Literal["knowledge", "action", "system"] = "knowledge"
    title: str
    description: str | None = None
    value: str | None = None
    person_ref: str | None = None
    proposed_fact_key: str | None = None
    semantic_type: str | None = None
    fact_type: str | None = None
    span_text: str | None = None
    confidence: float | None = None
    tool: str | None = None
    metadata: dict[str, Any] | None = None
    key: str | None = None

    @model_validator(mode="after")
    def _validate_kind_keys(self) -> SuggestionResult:
        if self.kind == "system":
            # Prefer action for chip surface; keep schedule tools working
            self.kind = "action"
        if self.kind == "knowledge":
            key = self.proposed_fact_key or self.key
            normalized = normalize_knowledge_fact_key(key, semantic_type=self.semantic_type)
            if not normalized and self.semantic_type:
                normalized = normalize_knowledge_fact_key(
                    f"custom:{self.semantic_type}", semantic_type=self.semantic_type
                )
            if not normalized:
                # Drop invalid knowledge by blanking key — store path will skip
                self.proposed_fact_key = None
            else:
                self.proposed_fact_key = normalized
        return self


class ExtractResult(BaseModel):
    people: list[PersonResult] = Field(default_factory=list)
    entities: list[EntityResult] = Field(default_factory=list)


class SuggestResult(BaseModel):
    suggestions: list[SuggestionResult] = Field(default_factory=list)
    suggested_categories: list[str] = Field(default_factory=list)


class AISchemaOutput(BaseModel):
    """Full monolithic LLM response (used before/after split-call merge)."""

    summary: str | None = None
    tags: list[str] | None = None
    topics: list[str] | None = None
    connections: list[str] | None = None
    why_it_matters: str | None = None
    keyword_summary: str | None = None
    temporal_refs: list[str] | None = None
    people_refs: list[str] | None = None
    place_refs: list[str] | None = None
    intent_tags: list[str] | None = None
    people: list[PersonResult] | None = None
    suggestions: list[SuggestionResult] | None = None
    entities: list[EntityResult] | None = None
    suggested_categories: list[str] | None = None

    def to_store_dict(self) -> dict[str, Any]:
        return self.model_dump(exclude_none=True)
