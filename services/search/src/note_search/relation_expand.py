"""One-hop person/fact/topic relationship expansion for hybrid retrieval."""

from __future__ import annotations

import re
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from note_db.models import (
    Lifecycle,
    MemoryFact,
    MemoryObject,
    MemoryPerson,
    Person,
    Understanding,
)
from note_search.service import SearchResult

_MAX_EXPANDED = 12


def _normalize(name: str) -> str:
    return re.sub(r"\s+", " ", name.strip().lower())


_RELATIONSHIP_ALIASES: dict[str, set[str]] = {
    "mother": {"mum", "mom", "mama", "mother"},
    "father": {"dad", "papa", "father"},
    "self": {"me", "i", "myself", "self"},
}


async def resolve_people_from_query(
    session: AsyncSession,
    user_id: UUID,
    query: str,
    must_include: list[str] | None = None,
) -> list[Person]:
    """Match query tokens / must_include against the user's people registry."""
    people = (
        await session.scalars(
            select(Person).where(
                Person.user_id == user_id,
                Person.merged_into_id.is_(None),
            )
        )
    ).all()
    if not people:
        return []

    needles: set[str] = set()
    for token in must_include or []:
        needles.add(_normalize(token))
    for match in re.finditer(r"\b([A-Za-z][a-z]{1,24})\b", query):
        needles.add(_normalize(match.group(1)))
    lower_q = query.lower()
    for aliases in _RELATIONSHIP_ALIASES.values():
        for alias in aliases:
            if re.search(rf"\b{re.escape(alias)}\b", lower_q):
                needles.add(alias)

    matched: list[Person] = []
    seen: set[UUID] = set()
    for person in people:
        names = {_normalize(person.display_name)}
        if person.relationship:
            names.add(_normalize(person.relationship))
            for alias in _RELATIONSHIP_ALIASES.get(person.relationship.lower(), set()):
                names.add(alias)
        if person.aliases:
            names.update(_normalize(a) for a in person.aliases if a)

        if names & needles:
            if person.id not in seen:
                matched.append(person)
                seen.add(person.id)
    return matched


async def expand_related_memories(
    session: AsyncSession,
    user_id: UUID,
    query: str,
    *,
    must_include: list[str] | None = None,
    seed_results: list[SearchResult] | None = None,
    limit: int = _MAX_EXPANDED,
) -> list[SearchResult]:
    """
    Expand one hop from people mentioned in the query:
    person → facts + memory_people → sibling memories sharing person or topics.
    """
    people = await resolve_people_from_query(session, user_id, query, must_include)
    if not people and seed_results:
        # Infer people from seed memory links when query has no clear person token
        seed_ids = [r.memory_id for r in seed_results[:8] if r.memory_id]
        if seed_ids:
            linked = (
                await session.scalars(
                    select(MemoryPerson).where(MemoryPerson.memory_object_id.in_(seed_ids))
                )
            ).all()
            person_ids = {link.person_id for link in linked}
            if person_ids:
                people = list(
                    (
                        await session.scalars(
                            select(Person).where(
                                Person.user_id == user_id,
                                Person.id.in_(person_ids),
                                Person.merged_into_id.is_(None),
                            )
                        )
                    ).all()
                )

    if not people:
        return []

    person_ids = [p.id for p in people]
    seed_memory_ids: set[UUID] = set()

    # Facts for these people
    facts = (
        await session.scalars(
            select(MemoryFact).where(
                MemoryFact.user_id == user_id,
                MemoryFact.person_id.in_(person_ids),
            )
        )
    ).all()
    for fact in facts:
        for raw_id in fact.source_memory_ids or []:
            try:
                seed_memory_ids.add(UUID(str(raw_id)))
            except ValueError:
                continue

    # Direct memory↔person links
    links = (
        await session.scalars(
            select(MemoryPerson).where(MemoryPerson.person_id.in_(person_ids))
        )
    ).all()
    for link in links:
        seed_memory_ids.add(link.memory_object_id)

    if not seed_memory_ids:
        return []

    # Topics from seed memories
    understandings = (
        await session.scalars(
            select(Understanding).where(
                Understanding.memory_object_id.in_(list(seed_memory_ids)),
                Understanding.superseded_by.is_(None),
            )
        )
    ).all()
    topic_keys: set[str] = set()
    for u in understandings:
        for field in (u.topics, u.tags):
            if isinstance(field, list):
                topic_keys.update(str(t).strip().lower() for t in field if t)

    expanded_ids: set[UUID] = set(seed_memory_ids)

    # One hop: other memories sharing person_id
    more_links = (
        await session.scalars(
            select(MemoryPerson).where(MemoryPerson.person_id.in_(person_ids))
        )
    ).all()
    for link in more_links:
        expanded_ids.add(link.memory_object_id)

    # One hop: memories with overlapping topics
    if topic_keys:
        all_u = (
            await session.scalars(
                select(Understanding)
                .join(MemoryObject, Understanding.memory_object_id == MemoryObject.id)
                .where(
                    MemoryObject.user_id == user_id,
                    MemoryObject.lifecycle != Lifecycle.deleted,
                    Understanding.superseded_by.is_(None),
                )
            )
        ).all()
        for u in all_u:
            topics = set()
            for field in (u.topics, u.tags):
                if isinstance(field, list):
                    topics.update(str(t).strip().lower() for t in field if t)
            if topics & topic_keys:
                expanded_ids.add(u.memory_object_id)

    # Exclude already-strong seed results to keep this list as an expansion channel
    already = {r.memory_id for r in (seed_results or []) if r.memory_id}
    candidate_ids = [mid for mid in expanded_ids if mid not in already][: limit * 2]
    # Always include seed memory ids that may be weak/missing from FTS
    for mid in seed_memory_ids:
        if mid not in already and mid not in candidate_ids:
            candidate_ids.append(mid)
    candidate_ids = candidate_ids[:limit]

    if not candidate_ids:
        # Still return fact-backed seed memories even if already in seeds — weak score channel
        candidate_ids = list(seed_memory_ids)[:limit]

    memories = (
        await session.scalars(
            select(MemoryObject).where(
                MemoryObject.id.in_(candidate_ids),
                MemoryObject.user_id == user_id,
                MemoryObject.lifecycle != Lifecycle.deleted,
            )
        )
    ).all()

    results: list[SearchResult] = []
    for mem in memories:
        preview = mem.content_text or f"{mem.structured_title}: {mem.structured_value}"
        results.append(
            SearchResult(
                memory_id=mem.id,
                person_id=person_ids[0] if person_ids else None,
                content_preview=(preview or "")[:200],
                match_type="relation_expand",
                score=0.42,
                source_type="memory",
            )
        )

    # Also surface related facts as candidates
    for fact in facts[:6]:
        label = fact.display_label or fact.key.replace("_", " ")
        who = f"{fact.person} " if fact.person else ""
        results.append(
            SearchResult(
                fact_id=fact.id,
                person_id=fact.person_id,
                content_preview=f"{who}{label}: {fact.value}"[:200],
                match_type="relation_fact",
                score=0.48,
                source_type="fact",
            )
        )

    return results[:limit]
