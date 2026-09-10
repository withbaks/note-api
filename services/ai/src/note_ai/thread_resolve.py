"""Resolve a new memory into an existing or new thread."""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from note_core.hlc import HLCClock
from note_db.models import (
    Entity,
    Helper,
    HelperSource,
    HelperStatus,
    MemoryObject,
    MemoryPerson,
    MemoryThread,
    Thread,
    Understanding,
)


AUTO_ATTACH_SIM = 0.80
CONFIRM_SIM_LOW = 0.55
LOOKBACK_DAYS = 60


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _signature_from_understanding(
    understanding: Understanding | None,
    people_ids: list[str],
    place_values: list[str],
) -> dict:
    topics = list(understanding.topics or []) if understanding else []
    intent = []
    if understanding and isinstance(understanding.raw, dict):
        intent = list(understanding.raw.get("intent_tags") or [])
    return {
        "topics": topics,
        "people_ids": people_ids,
        "places": place_values,
        "intent_tags": intent,
    }


def _overlap_score(sig: dict, primary: dict | list | None) -> float:
    if not primary or not isinstance(primary, dict):
        return 0.0
    scores: list[float] = []
    for field in ("topics", "people_ids", "places", "intent_tags"):
        a = set(str(x).lower() for x in (sig.get(field) or []))
        b = set(str(x).lower() for x in (primary.get(field) or []))
        if not a and not b:
            continue
        if not a or not b:
            scores.append(0.0)
            continue
        scores.append(len(a & b) / len(a | b))
    if not scores:
        return 0.0
    return sum(scores) / len(scores)


async def resolve_thread(
    session: AsyncSession,
    obj: MemoryObject,
    *,
    embed_fn=None,
    complete_json_fn=None,
) -> Thread | None:
    """Attach memory to a thread (auto / confirm helper / create).

    Returns the thread if auto-attached or created; None if deferred to confirm chip.
    """
    understanding = await session.scalar(
        select(Understanding).where(
            Understanding.memory_object_id == obj.id,
            Understanding.superseded_by.is_(None),
        )
    )

    people_rows = (
        await session.scalars(
            select(MemoryPerson).where(MemoryPerson.memory_object_id == obj.id)
        )
    ).all()
    people_ids = [str(p.person_id) for p in people_rows]

    entities = (
        await session.scalars(select(Entity).where(Entity.memory_object_id == obj.id))
    ).all()
    places = [e.value for e in entities if e.entity_type == "place"]

    sig = _signature_from_understanding(understanding, people_ids, places)
    clock = HLCClock("server")
    hlc = clock.now().to_string()
    now = datetime.now(UTC)

    # Already linked?
    existing_link = await session.scalar(
        select(MemoryThread).where(MemoryThread.memory_object_id == obj.id)
    )
    if existing_link:
        thread = await session.get(Thread, existing_link.thread_id)
        if thread:
            await _refresh_thread_summary(
                session, thread, understanding, obj, embed_fn=embed_fn, complete_json_fn=complete_json_fn
            )
            return thread

    since = now - timedelta(days=LOOKBACK_DAYS)
    candidates = (
        await session.scalars(
            select(Thread).where(
                Thread.user_id == obj.user_id,
                Thread.status == "active",
                Thread.last_memory_at.is_not(None),
                Thread.last_memory_at >= since,
            )
        )
    ).all()

    mem_embedding: list[float] | None = None
    seed_text = (understanding.summary if understanding else None) or (obj.content_text or "")[:500]
    if embed_fn and seed_text.strip():
        try:
            embs = await embed_fn([seed_text])
            mem_embedding = embs[0] if embs else None
        except Exception:
            mem_embedding = None

    scored: list[tuple[float, Thread]] = []
    for thread in candidates:
        overlap = _overlap_score(sig, thread.primary_entities)
        emb_sim = 0.0
        if mem_embedding and thread.embedding:
            emb_sim = _cosine(mem_embedding, list(thread.embedding))
        score = max(overlap, emb_sim)
        # Boost when both agree
        if overlap >= 0.4 and emb_sim >= 0.4:
            score = max(score, (overlap + emb_sim) / 2 + 0.1)
        if score >= CONFIRM_SIM_LOW:
            scored.append((score, thread))

    scored.sort(key=lambda x: x[0], reverse=True)
    best_score, best = scored[0] if scored else (0.0, None)

    if best and best_score >= AUTO_ATTACH_SIM:
        await _attach_memory(session, obj, best, hlc, now, sig)
        await _refresh_thread_summary(
            session, best, understanding, obj, embed_fn=embed_fn, complete_json_fn=complete_json_fn
        )
        await _backfill_connections(session, understanding, best, obj.id)
        return best

    if best and best_score >= CONFIRM_SIM_LOW:
        # Mid-band: action helper for user confirm
        existing_helper = await session.scalar(
            select(Helper).where(
                Helper.memory_object_id == obj.id,
                Helper.key == "confirm_thread",
            )
        )
        title = f"Link to thread: {best.title}"
        meta = {"thread_id": str(best.id), "score": round(best_score, 3)}
        if existing_helper:
            existing_helper.title = title
            existing_helper.description = best.summary
            existing_helper.kind = "action"
            existing_helper.tool_name = "confirm_thread"
            existing_helper.helper_metadata = meta
            existing_helper.status = HelperStatus.suggested
            existing_helper.hlc = hlc
        else:
            session.add(
                Helper(
                    memory_object_id=obj.id,
                    key="confirm_thread",
                    title=title,
                    description=best.summary,
                    kind="action",
                    tool_name="confirm_thread",
                    helper_metadata=meta,
                    source=HelperSource.llm,
                    status=HelperStatus.suggested,
                    hlc=hlc,
                    confidence=best_score,
                )
            )
        return None

    # Create new thread
    title = _title_from_sig(sig, understanding, obj)
    thread = Thread(
        id=uuid4(),
        user_id=obj.user_id,
        title=title,
        status="active",
        summary=understanding.summary if understanding else None,
        primary_entities=sig,
        first_memory_at=obj.created_at or now,
        last_memory_at=obj.created_at or now,
        embedding=mem_embedding,
        hlc=hlc,
    )
    session.add(thread)
    await session.flush()
    await _attach_memory(session, obj, thread, hlc, now, sig)
    await _backfill_connections(session, understanding, thread, obj.id)
    return thread


async def _attach_memory(
    session: AsyncSession,
    obj: MemoryObject,
    thread: Thread,
    hlc: str,
    now: datetime,
    sig: dict,
) -> None:
    session.add(
        MemoryThread(
            memory_object_id=obj.id,
            thread_id=thread.id,
            hlc=hlc,
        )
    )
    thread.last_memory_at = obj.created_at or now
    if not thread.first_memory_at:
        thread.first_memory_at = obj.created_at or now
    # Merge entity signature
    primary = dict(thread.primary_entities or {}) if isinstance(thread.primary_entities, dict) else {}
    for field in ("topics", "people_ids", "places", "intent_tags"):
        merged = list(dict.fromkeys(list(primary.get(field) or []) + list(sig.get(field) or [])))
        primary[field] = merged[:40]
    thread.primary_entities = primary
    thread.hlc = hlc
    await session.flush()


async def _refresh_thread_summary(
    session: AsyncSession,
    thread: Thread,
    understanding: Understanding | None,
    obj: MemoryObject,
    *,
    embed_fn=None,
    complete_json_fn=None,
) -> None:
    new_bit = (understanding.summary if understanding else None) or (obj.content_text or "")[:300]
    if not new_bit.strip():
        return
    old = thread.summary or ""
    updated = old
    if complete_json_fn and old:
        try:
            result = await complete_json_fn(
                f'Update this thread summary with the new memory. Return JSON {{"summary":"...","title":"..."}}.\n'
                f"Old summary: {old}\nNew memory insight: {new_bit}"
            )
            if isinstance(result, dict):
                updated = result.get("summary") or old
                if result.get("title"):
                    thread.title = str(result["title"])[:200]
        except Exception:
            updated = f"{old.rstrip()} {new_bit}".strip()[:2000]
    elif not old:
        updated = new_bit[:2000]
    else:
        updated = f"{old.rstrip()} {new_bit}".strip()[:2000]
    thread.summary = updated
    if embed_fn and updated:
        try:
            embs = await embed_fn([updated])
            if embs:
                thread.embedding = embs[0]
        except Exception:
            pass
    await session.flush()


async def _backfill_connections(
    session: AsyncSession,
    understanding: Understanding | None,
    thread: Thread,
    memory_id: UUID,
) -> None:
    if not understanding:
        return
    peer_ids = (
        await session.scalars(
            select(MemoryThread.memory_object_id).where(
                MemoryThread.thread_id == thread.id,
                MemoryThread.memory_object_id != memory_id,
            )
        )
    ).all()
    connections = [f"thread:{thread.id}"] + [f"memory:{mid}" for mid in peer_ids[:20]]
    understanding.connections = connections
    await session.flush()


def _title_from_sig(sig: dict, understanding: Understanding | None, obj: MemoryObject) -> str:
    topics = sig.get("topics") or []
    if topics:
        return str(topics[0])[:120]
    if understanding and understanding.summary:
        return understanding.summary[:80]
    text = (obj.content_text or "").strip()
    return (text[:60] + "…") if len(text) > 60 else (text or "Thread")
