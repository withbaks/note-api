"""Staleness batch: enqueue 'Still true?' review helpers for volatile facts."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from note_core.hlc import HLCClock
from note_db.models import Helper, HelperSource, HelperStatus, MemoryFact, MemoryObject
from note_sync.server_sync import ServerSyncEmitter

# Days since last reaffirm before prompting
VOLATILITY_THRESHOLDS = {
    "time_bound": 30,
    "slow": 90,
    # static: never auto-prompt
}


async def enqueue_staleness_reviews(session: AsyncSession, *, limit: int = 50) -> int:
    """Create review helpers for facts past their volatility threshold.

    Returns number of review helpers created.
    """
    now = datetime.now(UTC)
    candidates = (
        await session.scalars(
            select(MemoryFact).where(
                MemoryFact.expected_volatility.in_(["slow", "time_bound"]),
                or_(
                    MemoryFact.expires_at.is_not(None),
                    MemoryFact.last_reaffirmed_at.is_not(None),
                    MemoryFact.updated_at.is_not(None),
                ),
            ).limit(limit * 3)
        )
    ).all()

    created = 0
    clock = HLCClock("server")
    emitter = ServerSyncEmitter(session)

    for fact in candidates:
        if created >= limit:
            break
        vol = fact.expected_volatility or "slow"
        days = VOLATILITY_THRESHOLDS.get(vol)
        if days is None:
            continue

        anchor = fact.last_reaffirmed_at or fact.updated_at or fact.created_at
        if not anchor:
            continue
        if anchor.tzinfo is None:
            anchor = anchor.replace(tzinfo=UTC)
        if fact.expires_at:
            exp = fact.expires_at if fact.expires_at.tzinfo else fact.expires_at.replace(tzinfo=UTC)
            stale = exp <= now
        else:
            stale = anchor <= now - timedelta(days=days)
        if not stale:
            continue

        # Pick a source memory to hang the helper on
        source_ids = fact.source_memory_ids or []
        mem: MemoryObject | None = None
        for sid in source_ids:
            try:
                from uuid import UUID

                mem = await session.get(MemoryObject, UUID(str(sid)))
            except Exception:
                mem = None
            if mem and mem.user_id == fact.user_id:
                break
        if not mem:
            mem = await session.scalar(
                select(MemoryObject)
                .where(MemoryObject.user_id == fact.user_id)
                .order_by(MemoryObject.created_at.desc())
                .limit(1)
            )
        if not mem:
            continue

        review_key = f"still_true:{fact.id}"
        existing = await session.scalar(
            select(Helper).where(
                Helper.memory_object_id == mem.id,
                Helper.key == review_key,
                Helper.status == HelperStatus.suggested,
            )
        )
        if existing:
            continue

        label = fact.display_label or fact.key.replace("_", " ")
        hlc = clock.now().to_string()
        helper = Helper(
            id=uuid4(),
            memory_object_id=mem.id,
            key=review_key,
            title=f"Still true? {label}",
            description=f"Is “{fact.value}” still accurate?",
            kind="action",
            proposed_fact_key=fact.key,
            value=fact.value,
            person=fact.person,
            person_id=fact.person_id,
            semantic_type=fact.semantic_type,
            tool_name="review_fact",
            helper_metadata={
                "review_kind": "staleness",
                "fact_id": str(fact.id),
                "fact_key": fact.key,
                "existing_value": fact.value,
            },
            source=HelperSource.llm,
            status=HelperStatus.suggested,
            confidence=0.5,
            hlc=hlc,
        )
        session.add(helper)
        await session.flush()
        await emitter.emit_helper(fact.user_id, helper, operation="create")
        created += 1

    return created
