"""Admin sync and devices."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from note_db.models import Device, PushToken, Session, SyncCursor, SyncMutation, User


async def list_devices(
    session: AsyncSession,
    *,
    user_id: UUID | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[dict], int]:
    filters = []
    if user_id:
        filters.append(Device.user_id == user_id)

    total = int(
        await session.scalar(select(func.count()).select_from(Device).where(*filters)) or 0
    )
    rows = (
        await session.scalars(
            select(Device)
            .where(*filters)
            .order_by(Device.last_seen_at.desc().nullslast(), Device.created_at.desc())
            .limit(min(limit, 200))
            .offset(max(offset, 0))
        )
    ).all()
    items = []
    for d in rows:
        user = await session.get(User, d.user_id)
        push = await session.scalar(
            select(PushToken).where(
                PushToken.user_id == d.user_id,
                PushToken.device_id == str(d.id),
            )
        )
        items.append(
            {
                "id": str(d.id),
                "user_id": str(d.user_id),
                "user_email": user.email if user else None,
                "name": d.name,
                "platform": d.platform,
                "last_seen_at": d.last_seen_at.isoformat() if d.last_seen_at else None,
                "has_push_token": push is not None,
                "created_at": d.created_at.isoformat(),
            }
        )
    return items, total


async def list_user_sessions(
    session: AsyncSession,
    user_id: UUID,
    *,
    active_only: bool = True,
) -> list[dict]:
    filters = [Session.user_id == user_id]
    if active_only:
        filters.append(Session.revoked_at.is_(None))

    rows = (
        await session.scalars(
            select(Session).where(*filters).order_by(Session.created_at.desc())
        )
    ).all()
    items = []
    for s in rows:
        device = await session.get(Device, s.device_id)
        items.append(
            {
                "id": str(s.id),
                "device_id": str(s.device_id),
                "device_name": device.name if device else None,
                "revoked_at": s.revoked_at.isoformat() if s.revoked_at else None,
                "created_at": s.created_at.isoformat(),
                "expires_at": s.expires_at.isoformat(),
            }
        )
    return items


async def user_sync_summary(session: AsyncSession, user_id: UUID) -> dict:
    cursors = (
        await session.scalars(select(SyncCursor).where(SyncCursor.user_id == user_id))
    ).all()
    recent_mutations = (
        await session.scalars(
            select(SyncMutation)
            .where(SyncMutation.user_id == user_id)
            .order_by(SyncMutation.applied_at.desc())
            .limit(20)
        )
    ).all()
    return {
        "user_id": str(user_id),
        "cursors": [
            {
                "device_id": str(c.device_id),
                "cursor": c.cursor,
                "updated_at": c.updated_at.isoformat(),
            }
            for c in cursors
        ],
        "recent_mutations": [
            {
                "id": str(m.id),
                "entity_type": m.entity_type,
                "entity_id": str(m.entity_id),
                "operation": m.operation,
                "applied_at": m.applied_at.isoformat(),
            }
            for m in recent_mutations
        ],
    }


async def force_hydrate_user(session: AsyncSession, user_id: UUID) -> dict:
    """Reset sync cursors so clients re-hydrate on next pull."""
    cursors = (
        await session.scalars(select(SyncCursor).where(SyncCursor.user_id == user_id))
    ).all()
    for c in cursors:
        c.cursor = "0"
    await session.flush()
    return {"ok": True, "cursors_reset": len(cursors)}


async def list_audit_logs(
    session: AsyncSession,
    *,
    actor_id: UUID | None = None,
    action: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[dict], int]:
    from note_db.models import AdminAuditLog

    filters = []
    if actor_id:
        filters.append(AdminAuditLog.actor_id == actor_id)
    if action:
        filters.append(AdminAuditLog.action == action)

    total = int(
        await session.scalar(
            select(func.count()).select_from(AdminAuditLog).where(*filters)
        )
        or 0
    )
    rows = (
        await session.scalars(
            select(AdminAuditLog)
            .where(*filters)
            .order_by(AdminAuditLog.created_at.desc())
            .limit(min(limit, 200))
            .offset(max(offset, 0))
        )
    ).all()
    items = []
    for log in rows:
        actor = await session.get(User, log.actor_id)
        items.append(
            {
                "id": str(log.id),
                "actor_id": str(log.actor_id),
                "actor_email": actor.email if actor else None,
                "action": log.action,
                "target_type": log.target_type,
                "target_id": str(log.target_id) if log.target_id else None,
                "metadata": log.metadata_,
                "created_at": log.created_at.isoformat(),
            }
        )
    return items, total
