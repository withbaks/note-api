"""Admin audit logging."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from note_db.models import AdminAuditLog


async def write_audit_log(
    session: AsyncSession,
    *,
    actor_id: UUID,
    action: str,
    target_type: str | None = None,
    target_id: UUID | None = None,
    metadata: dict | None = None,
) -> None:
    session.add(
        AdminAuditLog(
            actor_id=actor_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            metadata_=metadata,
        )
    )
