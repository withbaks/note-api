"""Post-extraction fact candidate resolution (dedupe / refine / new)."""

from __future__ import annotations

import math
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from note_ai.fact_keys import normalize_knowledge_fact_key
from note_helpers.facts import FactUpsertResult, upsert_fact, volatility_for_key
from note_db.models import MemoryFact


@dataclass
class FactCandidate:
    fact_key: str
    value: str
    person_id: UUID | None
    person_label: str | None = None
    display_label: str | None = None
    fact_type: str = "attribute"
    semantic_type: str | None = None
    confidence: float = 0.0


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _canonical_sentence(key: str, value: str, person_label: str | None) -> str:
    who = person_label or "self"
    return f"{who}: {key} = {value}".strip()


def _classify_similarity(sim: float, existing_value: str, new_value: str) -> str:
    """Cheap heuristic classifier without an extra LLM call."""
    ev = (existing_value or "").strip().lower()
    nv = (new_value or "").strip().lower()
    if ev == nv:
        return "same_fact_reworded"
    if sim >= 0.92:
        return "same_fact_reworded"
    if sim >= 0.78:
        # Shared tokens → refinement; else treat as reworded if very close
        ev_tokens = set(ev.split())
        nv_tokens = set(nv.split())
        if ev_tokens & nv_tokens:
            return "refinement"
        return "same_fact_reworded"
    if sim >= 0.55:
        return "refinement"
    return "unrelated"


async def resolve_fact_candidate(
    session: AsyncSession,
    *,
    user_id: UUID,
    memory_id: UUID,
    candidate: FactCandidate,
    embedding: list[float] | None = None,
    allow_conflict_overwrite: bool = True,
) -> FactUpsertResult | None:
    """Resolve a knowledge candidate against existing facts, then upsert.

    1. Exact (person_id, fact_key) → append-only upsert
    2. Else compare embeddings among that person's facts
    3. Band → reworded | refinement | unrelated
    """
    fact_key = normalize_knowledge_fact_key(
        candidate.fact_key, semantic_type=candidate.semantic_type
    )
    if not fact_key:
        return None

    value = (candidate.value or "").strip()
    if not value:
        return None

    # Exact key match path
    exact = await session.scalar(
        select(MemoryFact).where(
            MemoryFact.user_id == user_id,
            MemoryFact.key == fact_key,
            MemoryFact.person_id == candidate.person_id,
        )
    )
    if exact is not None:
        return await upsert_fact(
            session,
            user_id=user_id,
            fact_key=fact_key,
            value=value,
            person_id=candidate.person_id,
            person_label=candidate.person_label,
            memory_id=memory_id,
            display_label=candidate.display_label,
            fact_type=candidate.fact_type,
            semantic_type=candidate.semantic_type,
            allow_conflict_overwrite=allow_conflict_overwrite,
            expected_volatility=volatility_for_key(fact_key, candidate.semantic_type),
            embedding=embedding,
        )

    # Embedding similarity against other facts for this person
    if embedding and candidate.person_id is not None:
        peers = (
            await session.scalars(
                select(MemoryFact).where(
                    MemoryFact.user_id == user_id,
                    MemoryFact.person_id == candidate.person_id,
                    MemoryFact.embedding.is_not(None),
                )
            )
        ).all()
        best: MemoryFact | None = None
        best_sim = 0.0
        for peer in peers:
            if not peer.embedding:
                continue
            sim = _cosine(embedding, list(peer.embedding))
            if sim > best_sim:
                best_sim = sim
                best = peer

        if best is not None and best_sim >= 0.55:
            decision = _classify_similarity(best_sim, best.value, value)
            if decision == "same_fact_reworded":
                return await upsert_fact(
                    session,
                    user_id=user_id,
                    fact_key=best.key,
                    value=best.value,  # keep existing wording
                    person_id=candidate.person_id,
                    person_label=candidate.person_label,
                    memory_id=memory_id,
                    display_label=candidate.display_label or best.display_label,
                    fact_type=best.fact_type,
                    semantic_type=best.semantic_type or candidate.semantic_type,
                    allow_conflict_overwrite=True,
                    expected_volatility=best.expected_volatility,
                    embedding=embedding,
                )
            if decision == "refinement":
                return await upsert_fact(
                    session,
                    user_id=user_id,
                    fact_key=best.key,
                    value=value,
                    person_id=candidate.person_id,
                    person_label=candidate.person_label,
                    memory_id=memory_id,
                    display_label=candidate.display_label or best.display_label,
                    fact_type=best.fact_type,
                    semantic_type=best.semantic_type or candidate.semantic_type,
                    allow_conflict_overwrite=True,
                    expected_volatility=best.expected_volatility,
                    embedding=embedding,
                )
            # unrelated → fall through to new fact

    return await upsert_fact(
        session,
        user_id=user_id,
        fact_key=fact_key,
        value=value,
        person_id=candidate.person_id,
        person_label=candidate.person_label,
        memory_id=memory_id,
        display_label=candidate.display_label,
        fact_type=candidate.fact_type,
        semantic_type=candidate.semantic_type,
        allow_conflict_overwrite=allow_conflict_overwrite,
        expected_volatility=volatility_for_key(fact_key, candidate.semantic_type),
        embedding=embedding,
    )


async def embed_fact_sentence(
    key: str,
    value: str,
    person_label: str | None,
    *,
    session: AsyncSession | None = None,
    user_id: UUID | None = None,
    memory_id: UUID | None = None,
) -> list[float] | None:
    from note_ai.llm_provider import embed_texts

    sentence = _canonical_sentence(key, value, person_label)
    embeddings = await embed_texts(
        [sentence],
        session=session,
        user_id=user_id,
        memory_id=memory_id,
        operation="fact_embed",
    )
    return embeddings[0] if embeddings and embeddings[0] else None
