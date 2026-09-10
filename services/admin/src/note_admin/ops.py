"""Admin ops: AI jobs, ingest, health, metrics."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from note_db.models import AIJob, AIState, IngestItem, MemoryObject, User
from note_queue import enqueue_ai_job, get_redis_pool


async def list_ai_jobs(
    session: AsyncSession,
    *,
    state: str | None = None,
    user_id: UUID | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[dict], int]:
    filters = []
    if state:
        filters.append(AIJob.state == AIState(state))
    if user_id:
        filters.append(MemoryObject.user_id == user_id)

    base = select(AIJob).join(MemoryObject, MemoryObject.id == AIJob.memory_object_id)
    if filters:
        base = base.where(*filters)

    total = int(
        await session.scalar(
            select(func.count())
            .select_from(AIJob)
            .join(MemoryObject, MemoryObject.id == AIJob.memory_object_id)
            .where(*filters)
        )
        or 0
    )
    rows = (
        await session.scalars(
            base.options(selectinload(AIJob.memory_object))
            .order_by(AIJob.updated_at.desc())
            .limit(min(limit, 200))
            .offset(max(offset, 0))
        )
    ).all()

    items = []
    for job in rows:
        mem = job.memory_object
        user = await session.get(User, mem.user_id) if mem else None
        items.append(
            {
                "id": str(job.id),
                "memory_object_id": str(job.memory_object_id),
                "state": job.state.value if hasattr(job.state, "value") else str(job.state),
                "attempts": job.attempts,
                "max_attempts": job.max_attempts,
                "last_error": job.last_error,
                "next_retry_at": job.next_retry_at.isoformat() if job.next_retry_at else None,
                "created_at": job.created_at.isoformat(),
                "updated_at": job.updated_at.isoformat(),
                "user_id": str(mem.user_id) if mem else None,
                "user_email": user.email if user else None,
                "content_preview": (
                    (mem.content_text or mem.structured_title or "")[:120] if mem else None
                ),
            }
        )
    return items, total


async def retry_ai_job(session: AsyncSession, job_id: UUID) -> dict:
    job = await session.get(AIJob, job_id)
    if not job:
        raise ValueError("AI job not found")
    job.state = AIState.captured
    job.last_error = None
    job.next_retry_at = None
    await session.flush()
    pool = await get_redis_pool()
    await enqueue_ai_job(pool, str(job.memory_object_id))
    return {"ok": True, "job_id": str(job_id), "memory_object_id": str(job.memory_object_id)}


async def retry_failed_ai_jobs(session: AsyncSession, *, limit: int = 50) -> dict:
    cap = min(max(limit, 1), 200)
    rows = (
        await session.scalars(
            select(AIJob)
            .where(AIJob.state == AIState.failed)
            .order_by(AIJob.updated_at.desc())
            .limit(cap)
        )
    ).all()
    pool = await get_redis_pool()
    count = 0
    for job in rows:
        job.state = AIState.captured
        job.last_error = None
        job.next_retry_at = None
        await enqueue_ai_job(pool, str(job.memory_object_id))
        count += 1
    await session.flush()
    return {"ok": True, "retried": count}


async def list_ingest_items(
    session: AsyncSession,
    *,
    status: str | None = None,
    user_id: UUID | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[dict], int]:
    filters = []
    if status:
        filters.append(IngestItem.status == status)
    if user_id:
        filters.append(IngestItem.user_id == user_id)

    total = int(
        await session.scalar(
            select(func.count()).select_from(IngestItem).where(*filters)
        )
        or 0
    )
    rows = (
        await session.scalars(
            select(IngestItem)
            .where(*filters)
            .order_by(IngestItem.created_at.desc())
            .limit(min(limit, 200))
            .offset(max(offset, 0))
        )
    ).all()

    items = []
    for item in rows:
        user = await session.get(User, item.user_id)
        items.append(
            {
                "id": str(item.id),
                "user_id": str(item.user_id),
                "user_email": user.email if user else None,
                "memory_object_id": str(item.memory_object_id) if item.memory_object_id else None,
                "source_type": item.source_type,
                "source_url": item.source_url,
                "status": item.status,
                "title": item.title,
                "created_at": item.created_at.isoformat(),
            }
        )
    return items, total


async def retry_ingest_item(session: AsyncSession, item_id: UUID) -> dict:
    item = await session.get(IngestItem, item_id)
    if not item:
        raise ValueError("Ingest item not found")
    if not item.memory_object_id:
        raise ValueError("Ingest item has no linked memory")
    item.status = "pending"
    await session.flush()
    pool = await get_redis_pool()
    await enqueue_ai_job(pool, str(item.memory_object_id))
    return {"ok": True, "item_id": str(item_id)}


async def health_check(session: AsyncSession) -> dict:
    db_ok = False
    redis_ok = False
    try:
        await session.scalar(select(func.count()).select_from(User).limit(1))
        db_ok = True
    except Exception:
        pass
    try:
        pool = await get_redis_pool()
        await pool.ping()
        redis_ok = True
    except Exception:
        pass

    status = "ok" if db_ok and redis_ok else "degraded"
    return {
        "status": status,
        "api": "ok",
        "database": "ok" if db_ok else "error",
        "redis": "ok" if redis_ok else "error",
        "checked_at": datetime.now(UTC).isoformat(),
    }


async def overview_trends(session: AsyncSession, *, days: int = 7) -> dict:
    since = datetime.now(UTC) - timedelta(days=days)
    users = int(
        await session.scalar(
            select(func.count()).select_from(User).where(User.created_at >= since)
        )
        or 0
    )
    memories = int(
        await session.scalar(
            select(func.count()).select_from(MemoryObject).where(MemoryObject.created_at >= since)
        )
        or 0
    )
    ai_failures = int(
        await session.scalar(
            select(func.count())
            .select_from(AIJob)
            .where(AIJob.state == AIState.failed, AIJob.updated_at >= since)
        )
        or 0
    )
    return {"days": days, "users_created": users, "memories_created": memories, "ai_failures": ai_failures}


async def metrics_snapshot(session: AsyncSession) -> dict:
    ingest_pending = int(
        await session.scalar(
            select(func.count()).select_from(IngestItem).where(IngestItem.status == "pending")
        )
        or 0
    )
    ai_failed = int(
        await session.scalar(
            select(func.count()).select_from(AIJob).where(AIJob.state == AIState.failed)
        )
        or 0
    )
    ai_processing = int(
        await session.scalar(
            select(func.count()).select_from(AIJob).where(AIJob.state == AIState.processing)
        )
        or 0
    )
    return {
        "ingest_pending": ingest_pending,
        "ai_jobs_failed": ai_failed,
        "ai_jobs_processing": ai_processing,
    }
