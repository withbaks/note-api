"""Shared fact upsert logic for helpers and AI auto-accept.

Facts are append-only via FactHistory: every create and value change records history.
Same-value reaffirmation bumps mention_count and last_reaffirmed_at.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from note_db.models import FactHistory, Helper, MemoryFact, User
from note_db.people import PeopleService

FACT_FROM_HELPER = {
    "is_shoe_size": ("shoe_size", "measurement"),
    "is_phone_number": ("phone_number", "contact"),
    "is_mothers_phone_no": ("phone_number", "contact"),
    "is_monthly_budget": ("monthly_budget", "preference"),
    "is_age": ("age", "person_meta"),
    "is_birthday": ("birthday", "event"),
}

# static | slow | time_bound
VOLATILITY_BY_KEY: dict[str, str] = {
    "full_name": "static",
    "birthday": "static",
    "phone_number": "slow",
    "email": "slow",
    "shoe_size": "slow",
    "age": "time_bound",
    "monthly_budget": "time_bound",
}

VOLATILITY_BY_SEMANTIC: dict[str, str] = {
    "person_meta": "slow",
    "contact": "slow",
    "measurement": "slow",
    "health": "time_bound",
    "education": "slow",
    "event": "time_bound",
    "place": "slow",
    "product": "slow",
    "preference": "slow",
    "identity": "static",
}


def volatility_for_key(fact_key: str, semantic_type: str | None = None) -> str:
    if fact_key.startswith("custom:"):
        return "slow"
    if fact_key in VOLATILITY_BY_KEY:
        return VOLATILITY_BY_KEY[fact_key]
    if fact_key.startswith("preference:"):
        return "slow"
    if semantic_type and semantic_type in VOLATILITY_BY_SEMANTIC:
        return VOLATILITY_BY_SEMANTIC[semantic_type]
    return "slow"


class FactUpsertResult:
    def __init__(
        self,
        *,
        fact: MemoryFact | None = None,
        conflict: bool = False,
        existing_value: str | None = None,
        reaffirmed: bool = False,
        refined: bool = False,
        created: bool = False,
    ) -> None:
        self.fact = fact
        self.conflict = conflict
        self.existing_value = existing_value
        self.reaffirmed = reaffirmed
        self.refined = refined
        self.created = created


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def upsert_fact_from_helper(
    session: AsyncSession,
    user_id: UUID,
    helper: Helper,
    memory_id: UUID,
    *,
    allow_conflict_overwrite: bool = False,
    expected_volatility: str | None = None,
    embedding: list[float] | None = None,
) -> FactUpsertResult:
    if helper.kind in ("system", "action"):
        return FactUpsertResult()

    fact_key = helper.proposed_fact_key
    semantic = helper.semantic_type
    if not fact_key and helper.key in FACT_FROM_HELPER:
        fact_key, semantic_default = FACT_FROM_HELPER[helper.key]
        semantic = semantic or semantic_default
    if not fact_key:
        return FactUpsertResult()

    user = await session.get(User, user_id)
    if not user:
        return FactUpsertResult()

    if helper.person_id:
        person_id = helper.person_id
        person_label = helper.person
    else:
        people = PeopleService(session)
        if helper.person:
            person = await people.resolve(user_id, display_name=helper.person, user=user)
        else:
            person = await people.ensure_self(user)
        person_id = person.id
        person_label = None if person.relationship == "self" else person.display_name.lower()
        helper.person_id = person_id
        helper.person = person_label
        helper.semantic_type = helper.semantic_type or semantic

    return await upsert_fact(
        session,
        user_id=user_id,
        fact_key=fact_key,
        value=helper.value or "",
        person_id=person_id,
        person_label=person_label,
        memory_id=memory_id,
        display_label=helper.title,
        fact_type=(helper.helper_metadata or {}).get("fact_type", "attribute"),
        semantic_type=helper.semantic_type or semantic,
        allow_conflict_overwrite=allow_conflict_overwrite,
        expected_volatility=expected_volatility
        or volatility_for_key(fact_key, helper.semantic_type or semantic),
        embedding=embedding,
        changed_by="ai",
    )


async def upsert_fact(
    session: AsyncSession,
    *,
    user_id: UUID,
    fact_key: str,
    value: str,
    person_id: UUID | None,
    memory_id: UUID,
    person_label: str | None = None,
    display_label: str | None = None,
    fact_type: str = "attribute",
    semantic_type: str | None = None,
    allow_conflict_overwrite: bool = True,
    expected_volatility: str | None = None,
    embedding: list[float] | None = None,
    changed_by: str = "ai",
) -> FactUpsertResult:
    existing = await session.scalar(
        select(MemoryFact).where(
            MemoryFact.user_id == user_id,
            MemoryFact.key == fact_key,
            MemoryFact.person_id == person_id,
        )
    )

    new_value = (value or "").strip()
    volatility = expected_volatility or volatility_for_key(fact_key, semantic_type)
    now = _now()

    if existing:
        old_value = (existing.value or "").strip()
        sources = list(set((existing.source_memory_ids or []) + [str(memory_id)]))
        existing.source_memory_ids = sources
        existing.mention_count = int(existing.mention_count or 1) + 1
        existing.last_reaffirmed_at = now
        if embedding is not None:
            existing.embedding = embedding
        if display_label:
            existing.display_label = display_label
        if semantic_type:
            existing.semantic_type = semantic_type
        if not existing.expected_volatility:
            existing.expected_volatility = volatility

        if old_value and new_value and old_value.lower() == new_value.lower():
            # Same value reaffirm — no history row needed for identical value
            await session.flush()
            return FactUpsertResult(fact=existing, reaffirmed=True)

        if old_value and new_value and old_value.lower() != new_value.lower():
            if not allow_conflict_overwrite:
                return FactUpsertResult(conflict=True, existing_value=old_value)
            session.add(
                FactHistory(
                    fact_id=existing.id,
                    value=old_value,
                    source_memory_id=memory_id,
                    changed_by=changed_by,
                )
            )
            existing.value = new_value
            await session.flush()
            return FactUpsertResult(fact=existing, refined=True)

        # Empty old or empty new — still record history if we had a prior value
        if old_value and new_value != old_value:
            session.add(
                FactHistory(
                    fact_id=existing.id,
                    value=old_value,
                    source_memory_id=memory_id,
                    changed_by=changed_by,
                )
            )
            existing.value = new_value or existing.value
            await session.flush()
            return FactUpsertResult(fact=existing, refined=True)

        await session.flush()
        return FactUpsertResult(fact=existing, reaffirmed=True)

    fact = MemoryFact(
        user_id=user_id,
        key=fact_key,
        display_label=display_label,
        fact_type=fact_type,
        value=new_value,
        person=person_label,
        person_id=person_id,
        semantic_type=semantic_type,
        user_override=False,
        source_memory_ids=[str(memory_id)],
        mention_count=1,
        last_reaffirmed_at=now,
        expected_volatility=volatility,
        embedding=embedding,
    )
    session.add(fact)
    await session.flush()
    session.add(
        FactHistory(
            fact_id=fact.id,
            value=new_value,
            source_memory_id=memory_id,
            changed_by=changed_by,
        )
    )
    await session.flush()
    return FactUpsertResult(fact=fact, created=True)
