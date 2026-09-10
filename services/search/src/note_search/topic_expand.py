"""Topic co-occurrence expansion into the hybrid candidate pool."""

from __future__ import annotations

from collections import Counter
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from note_db.models import Lifecycle, MemoryObject, Understanding
from note_search.service import SearchResult

_MAX_TOPIC_EXPAND = 8


async def expand_topic_cooccur(
    session: AsyncSession,
    user_id: UUID,
    seed_results: list[SearchResult],
    *,
    limit: int = _MAX_TOPIC_EXPAND,
) -> list[SearchResult]:
    """
    If top candidates share topics, pull additional memories from those topics.
    """
    seed_ids = [r.memory_id for r in seed_results[:8] if r.memory_id]
    if not seed_ids:
        return []

    understandings = (
        await session.scalars(
            select(Understanding).where(
                Understanding.memory_object_id.in_(seed_ids),
                Understanding.superseded_by.is_(None),
            )
        )
    ).all()

    topic_counts: Counter[str] = Counter()
    for u in understandings:
        if isinstance(u.topics, list):
            for t in u.topics:
                key = str(t).strip().lower()
                if key:
                    topic_counts[key] += 1

    # Prefer topics shared by 2+ seed memories; fall back to top single topics
    shared = [t for t, c in topic_counts.most_common(6) if c >= 2]
    if not shared:
        shared = [t for t, _ in topic_counts.most_common(3)]
    if not shared:
        return []

    already = set(seed_ids)
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

    expand_ids: list[UUID] = []
    for u in all_u:
        if u.memory_object_id in already:
            continue
        topics = {str(t).strip().lower() for t in (u.topics or []) if t}
        if topics & set(shared):
            expand_ids.append(u.memory_object_id)
        if len(expand_ids) >= limit:
            break

    if not expand_ids:
        return []

    memories = (
        await session.scalars(
            select(MemoryObject).where(
                MemoryObject.id.in_(expand_ids),
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
                content_preview=(preview or "")[:200],
                match_type="topic_cooccur",
                score=0.38,
                source_type="memory",
            )
        )
    return results
