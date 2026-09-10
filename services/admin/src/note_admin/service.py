"""Staff-only admin APIs for the note-admin dashboard."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from note_admin.permissions import effective_admin_role
from note_db.models import (
    AIJob,
    AIState,
    AdminRole,
    Device,
    Friendship,
    Group,
    IngestItem,
    Lifecycle,
    MemoryObject,
    Session,
    User,
)


class OverviewResponse(BaseModel):
    users_total: int
    users_active: int
    users_deleted: int
    users_staff: int
    memories_total: int
    memories_active: int
    memories_deleted: int
    memories_ai_failed: int
    memories_ai_processing: int
    devices_total: int
    sessions_active: int
    friendships_total: int
    groups_total: int
    ingest_pending: int
    ai_jobs_failed: int


class AdminUserListItem(BaseModel):
    id: UUID
    email: str | None
    username: str | None
    display_name: str | None
    is_staff: bool
    admin_role: str | None = None
    ai_enabled: bool
    deleted_at: datetime | None
    created_at: datetime
    memory_count: int = 0
    device_count: int = 0

    model_config = {"from_attributes": True}


class AdminUserDetail(AdminUserListItem):
    apple_sub: str | None
    avatar_url: str | None
    ai_use_notes_context: bool
    helper_auto_accept: bool
    notifications_enabled: bool
    updated_at: datetime
    devices: list[dict]
    recent_memories: list[dict]


class AdminUserUpdate(BaseModel):
    is_staff: bool | None = None
    admin_role: str | None = None
    ai_enabled: bool | None = None
    ai_use_notes_context: bool | None = None
    helper_auto_accept: bool | None = None
    notifications_enabled: bool | None = None
    restore: bool | None = Field(
        default=None, description="If true, clear deleted_at"
    )


class AdminMemoryListItem(BaseModel):
    id: UUID
    user_id: UUID
    type: str
    origin: str
    content_preview: str | None
    lifecycle: str
    ai_state: str
    favorite: bool
    created_at: datetime
    updated_at: datetime
    user_email: str | None = None
    user_username: str | None = None


class AdminMemoryDetail(AdminMemoryListItem):
    content_text: str | None
    structured_title: str | None
    structured_value: str | None
    media_uri: str | None
    media_type: str | None
    visibility: str
    version: int
    deleted_at: datetime | None
    understandings: list[dict]
    helpers: list[dict]
    ai_jobs: list[dict]


class AdminService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def overview(self) -> OverviewResponse:
        s = self.session

        async def count(stmt) -> int:
            return int(await s.scalar(stmt) or 0)

        users_total = await count(select(func.count()).select_from(User))
        users_deleted = await count(
            select(func.count()).select_from(User).where(User.deleted_at.is_not(None))
        )
        users_staff = await count(
            select(func.count()).select_from(User).where(User.is_staff.is_(True))
        )
        memories_total = await count(select(func.count()).select_from(MemoryObject))
        memories_active = await count(
            select(func.count())
            .select_from(MemoryObject)
            .where(MemoryObject.lifecycle == Lifecycle.active)
        )
        memories_deleted = await count(
            select(func.count())
            .select_from(MemoryObject)
            .where(MemoryObject.lifecycle == Lifecycle.deleted)
        )
        memories_ai_failed = await count(
            select(func.count())
            .select_from(MemoryObject)
            .where(MemoryObject.ai_state == AIState.failed)
        )
        memories_ai_processing = await count(
            select(func.count())
            .select_from(MemoryObject)
            .where(MemoryObject.ai_state == AIState.processing)
        )
        devices_total = await count(select(func.count()).select_from(Device))
        sessions_active = await count(
            select(func.count()).select_from(Session).where(Session.revoked_at.is_(None))
        )
        friendships_total = await count(select(func.count()).select_from(Friendship))
        groups_total = await count(select(func.count()).select_from(Group))
        ingest_pending = await count(
            select(func.count())
            .select_from(IngestItem)
            .where(IngestItem.status == "pending")
        )
        ai_jobs_failed = await count(
            select(func.count()).select_from(AIJob).where(AIJob.state == AIState.failed)
        )

        return OverviewResponse(
            users_total=users_total,
            users_active=users_total - users_deleted,
            users_deleted=users_deleted,
            users_staff=users_staff,
            memories_total=memories_total,
            memories_active=memories_active,
            memories_deleted=memories_deleted,
            memories_ai_failed=memories_ai_failed,
            memories_ai_processing=memories_ai_processing,
            devices_total=devices_total,
            sessions_active=sessions_active,
            friendships_total=friendships_total,
            groups_total=groups_total,
            ingest_pending=ingest_pending,
            ai_jobs_failed=ai_jobs_failed,
        )

    async def list_users(
        self,
        *,
        q: str | None = None,
        staff_only: bool = False,
        include_deleted: bool = True,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[AdminUserListItem], int]:
        filters = []
        if staff_only:
            filters.append(User.is_staff.is_(True))
        if not include_deleted:
            filters.append(User.deleted_at.is_(None))
        if q:
            like = f"%{q.strip()}%"
            filters.append(
                or_(
                    User.email.ilike(like),
                    User.username.ilike(like),
                    User.display_name.ilike(like),
                )
            )

        total = int(
            await self.session.scalar(
                select(func.count()).select_from(User).where(*filters)
            )
            or 0
        )

        rows = (
            await self.session.execute(
                select(User)
                .where(*filters)
                .order_by(User.created_at.desc())
                .limit(min(limit, 200))
                .offset(max(offset, 0))
            )
        ).scalars().all()

        items: list[AdminUserListItem] = []
        for user in rows:
            memory_count = int(
                await self.session.scalar(
                    select(func.count())
                    .select_from(MemoryObject)
                    .where(MemoryObject.user_id == user.id)
                )
                or 0
            )
            device_count = int(
                await self.session.scalar(
                    select(func.count()).select_from(Device).where(Device.user_id == user.id)
                )
                or 0
            )
            items.append(
                AdminUserListItem(
                    id=user.id,
                    email=user.email,
                    username=user.username,
                    display_name=user.display_name,
                    is_staff=user.is_staff,
                    admin_role=effective_admin_role(user).value,
                    ai_enabled=user.ai_enabled,
                    deleted_at=user.deleted_at,
                    created_at=user.created_at,
                    memory_count=memory_count,
                    device_count=device_count,
                )
            )
        return items, total

    async def get_user(self, user_id: UUID) -> AdminUserDetail:
        user = await self.session.get(User, user_id)
        if not user:
            raise ValueError("User not found")

        devices = (
            await self.session.scalars(
                select(Device).where(Device.user_id == user_id).order_by(Device.created_at.desc())
            )
        ).all()
        recent = (
            await self.session.scalars(
                select(MemoryObject)
                .where(MemoryObject.user_id == user_id)
                .order_by(MemoryObject.updated_at.desc())
                .limit(20)
            )
        ).all()
        memory_count = int(
            await self.session.scalar(
                select(func.count())
                .select_from(MemoryObject)
                .where(MemoryObject.user_id == user_id)
            )
            or 0
        )

        return AdminUserDetail(
            id=user.id,
            email=user.email,
            username=user.username,
            display_name=user.display_name,
            is_staff=user.is_staff,
            admin_role=effective_admin_role(user).value,
            ai_enabled=user.ai_enabled,
            deleted_at=user.deleted_at,
            created_at=user.created_at,
            memory_count=memory_count,
            device_count=len(devices),
            apple_sub=user.apple_sub,
            avatar_url=user.avatar_url,
            ai_use_notes_context=user.ai_use_notes_context,
            helper_auto_accept=user.helper_auto_accept,
            notifications_enabled=user.notifications_enabled,
            updated_at=user.updated_at,
            devices=[
                {
                    "id": str(d.id),
                    "name": d.name,
                    "platform": d.platform,
                    "last_seen_at": d.last_seen_at.isoformat() if d.last_seen_at else None,
                    "created_at": d.created_at.isoformat(),
                }
                for d in devices
            ],
            recent_memories=[
                {
                    "id": str(m.id),
                    "type": m.type.value if hasattr(m.type, "value") else str(m.type),
                    "lifecycle": m.lifecycle.value if hasattr(m.lifecycle, "value") else str(m.lifecycle),
                    "ai_state": m.ai_state.value if hasattr(m.ai_state, "value") else str(m.ai_state),
                    "preview": (m.content_text or m.structured_title or "")[:120] or None,
                    "updated_at": m.updated_at.isoformat(),
                }
                for m in recent
            ],
        )

    async def update_user(self, user_id: UUID, req: AdminUserUpdate) -> AdminUserDetail:
        user = await self.session.get(User, user_id)
        if not user:
            raise ValueError("User not found")
        if req.is_staff is not None:
            user.is_staff = req.is_staff
        if req.admin_role is not None:
            user.admin_role = AdminRole(req.admin_role)
            if user.admin_role != AdminRole.viewer:
                user.is_staff = True
        if req.ai_enabled is not None:
            user.ai_enabled = req.ai_enabled
        if req.ai_use_notes_context is not None:
            user.ai_use_notes_context = req.ai_use_notes_context
        if req.helper_auto_accept is not None:
            user.helper_auto_accept = req.helper_auto_accept
        if req.notifications_enabled is not None:
            user.notifications_enabled = req.notifications_enabled
        if req.restore:
            user.deleted_at = None
        await self.session.flush()
        return await self.get_user(user_id)

    async def soft_delete_user(self, user_id: UUID) -> None:
        from datetime import UTC

        user = await self.session.get(User, user_id)
        if not user:
            raise ValueError("User not found")
        now = datetime.now(UTC)
        user.deleted_at = now
        sessions = (
            await self.session.scalars(
                select(Session).where(Session.user_id == user_id, Session.revoked_at.is_(None))
            )
        ).all()
        for s in sessions:
            s.revoked_at = now

    async def revoke_user_sessions(self, user_id: UUID) -> int:
        from datetime import UTC

        user = await self.session.get(User, user_id)
        if not user:
            raise ValueError("User not found")
        now = datetime.now(UTC)
        sessions = (
            await self.session.scalars(
                select(Session).where(Session.user_id == user_id, Session.revoked_at.is_(None))
            )
        ).all()
        for s in sessions:
            s.revoked_at = now
        return len(sessions)

    async def list_memories(
        self,
        *,
        q: str | None = None,
        user_id: UUID | None = None,
        lifecycle: str | None = None,
        ai_state: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[AdminMemoryListItem], int]:
        filters = []
        if user_id:
            filters.append(MemoryObject.user_id == user_id)
        if lifecycle:
            filters.append(MemoryObject.lifecycle == Lifecycle(lifecycle))
        if ai_state:
            filters.append(MemoryObject.ai_state == AIState(ai_state))
        if q:
            like = f"%{q.strip()}%"
            filters.append(
                or_(
                    MemoryObject.content_text.ilike(like),
                    MemoryObject.structured_title.ilike(like),
                    MemoryObject.structured_value.ilike(like),
                )
            )

        total = int(
            await self.session.scalar(
                select(func.count()).select_from(MemoryObject).where(*filters)
            )
            or 0
        )

        rows = (
            await self.session.execute(
                select(MemoryObject, User)
                .join(User, User.id == MemoryObject.user_id)
                .where(*filters)
                .order_by(MemoryObject.updated_at.desc())
                .limit(min(limit, 200))
                .offset(max(offset, 0))
            )
        ).all()

        items: list[AdminMemoryListItem] = []
        for mem, user in rows:
            preview = mem.content_text or mem.structured_title or mem.structured_value
            items.append(
                AdminMemoryListItem(
                    id=mem.id,
                    user_id=mem.user_id,
                    type=mem.type.value if hasattr(mem.type, "value") else str(mem.type),
                    origin=mem.origin,
                    content_preview=(preview[:160] if preview else None),
                    lifecycle=mem.lifecycle.value if hasattr(mem.lifecycle, "value") else str(mem.lifecycle),
                    ai_state=mem.ai_state.value if hasattr(mem.ai_state, "value") else str(mem.ai_state),
                    favorite=mem.favorite,
                    created_at=mem.created_at,
                    updated_at=mem.updated_at,
                    user_email=user.email,
                    user_username=user.username,
                )
            )
        return items, total

    async def get_memory(self, memory_id: UUID) -> AdminMemoryDetail:
        mem = await self.session.scalar(
            select(MemoryObject)
            .where(MemoryObject.id == memory_id)
            .options(
                selectinload(MemoryObject.understandings),
                selectinload(MemoryObject.helpers),
                selectinload(MemoryObject.ai_jobs),
            )
        )
        if not mem:
            raise ValueError("Memory not found")
        user = await self.session.get(User, mem.user_id)
        preview = mem.content_text or mem.structured_title or mem.structured_value

        return AdminMemoryDetail(
            id=mem.id,
            user_id=mem.user_id,
            type=mem.type.value if hasattr(mem.type, "value") else str(mem.type),
            origin=mem.origin,
            content_preview=(preview[:160] if preview else None),
            lifecycle=mem.lifecycle.value if hasattr(mem.lifecycle, "value") else str(mem.lifecycle),
            ai_state=mem.ai_state.value if hasattr(mem.ai_state, "value") else str(mem.ai_state),
            favorite=mem.favorite,
            created_at=mem.created_at,
            updated_at=mem.updated_at,
            user_email=user.email if user else None,
            user_username=user.username if user else None,
            content_text=mem.content_text,
            structured_title=mem.structured_title,
            structured_value=mem.structured_value,
            media_uri=mem.media_uri,
            media_type=mem.media_type,
            visibility=mem.visibility.value if hasattr(mem.visibility, "value") else str(mem.visibility),
            version=mem.version,
            deleted_at=mem.deleted_at,
            understandings=[
                {
                    "id": str(u.id),
                    "summary": u.summary,
                    "tags": u.tags,
                    "created_at": u.created_at.isoformat(),
                }
                for u in sorted(mem.understandings, key=lambda x: x.created_at, reverse=True)[:5]
            ],
            helpers=[
                {
                    "id": str(h.id),
                    "key": h.key,
                    "value": h.value,
                    "status": h.status.value if hasattr(h.status, "value") else str(h.status),
                    "semantic_type": h.semantic_type,
                }
                for h in mem.helpers[:40]
            ],
            ai_jobs=[
                {
                    "id": str(j.id),
                    "state": j.state.value if hasattr(j.state, "value") else str(j.state),
                    "attempts": j.attempts,
                    "last_error": j.last_error,
                    "created_at": j.created_at.isoformat(),
                }
                for j in sorted(mem.ai_jobs, key=lambda x: x.created_at, reverse=True)[:10]
            ],
        )

    async def set_memory_lifecycle(self, memory_id: UUID, lifecycle: Lifecycle) -> AdminMemoryDetail:
        from datetime import UTC

        mem = await self.session.get(MemoryObject, memory_id)
        if not mem:
            raise ValueError("Memory not found")
        mem.lifecycle = lifecycle
        mem.deleted_at = datetime.now(UTC) if lifecycle == Lifecycle.deleted else None
        await self.session.flush()
        return await self.get_memory(memory_id)

    # --- delegated domain modules ---

    async def list_ai_jobs(self, **kwargs):
        from note_admin import ops

        return await ops.list_ai_jobs(self.session, **kwargs)

    async def retry_ai_job(self, job_id: UUID):
        from note_admin import ops

        return await ops.retry_ai_job(self.session, job_id)

    async def retry_failed_ai_jobs(self, limit: int = 50):
        from note_admin import ops

        return await ops.retry_failed_ai_jobs(self.session, limit=limit)

    async def list_ingest_items(self, **kwargs):
        from note_admin import ops

        return await ops.list_ingest_items(self.session, **kwargs)

    async def retry_ingest_item(self, item_id: UUID):
        from note_admin import ops

        return await ops.retry_ingest_item(self.session, item_id)

    async def health_check(self):
        from note_admin import ops

        return await ops.health_check(self.session)

    async def overview_trends(self, days: int = 7):
        from note_admin import ops

        return await ops.overview_trends(self.session, days=days)

    async def metrics_snapshot(self):
        from note_admin import ops

        return await ops.metrics_snapshot(self.session)

    async def list_user_helpers(self, user_id: UUID, **kwargs):
        from note_admin import content

        return await content.list_user_helpers(self.session, user_id, **kwargs)

    async def list_helpers(self, **kwargs):
        from note_admin import content

        return await content.list_helpers(self.session, **kwargs)

    async def get_helper(self, helper_id: UUID):
        from note_admin import content

        return await content.get_helper(self.session, helper_id)

    async def update_helper_status(self, helper_id: UUID, status: str):
        from note_admin import content

        return await content.update_helper_status(self.session, helper_id, status)

    async def list_user_facts(self, user_id: UUID, **kwargs):
        from note_admin import content

        return await content.list_user_facts(self.session, user_id, **kwargs)

    async def list_facts(self, **kwargs):
        from note_admin import content

        return await content.list_facts(self.session, **kwargs)

    async def list_user_people(self, user_id: UUID, **kwargs):
        from note_admin import content

        return await content.list_user_people(self.session, user_id, **kwargs)

    async def list_people(self, **kwargs):
        from note_admin import content

        return await content.list_people(self.session, **kwargs)

    async def list_user_categories(self, user_id: UUID, **kwargs):
        from note_admin import content

        return await content.list_user_categories(self.session, user_id, **kwargs)

    async def list_categories(self, **kwargs):
        from note_admin import content

        return await content.list_categories(self.session, **kwargs)

    async def global_search(self, q: str, limit: int = 5):
        from note_admin import content

        return await content.global_search(self.session, q, limit=limit)

    async def list_friendships(self, **kwargs):
        from note_admin import social

        return await social.list_friendships(self.session, **kwargs)

    async def list_groups(self, **kwargs):
        from note_admin import social

        return await social.list_groups(self.session, **kwargs)

    async def get_group(self, group_id: UUID):
        from note_admin import social

        return await social.get_group(self.session, group_id)

    async def list_shares(self, **kwargs):
        from note_admin import social

        return await social.list_shares(self.session, **kwargs)

    async def revoke_share(self, share_id: UUID):
        from note_admin import social

        return await social.revoke_share(self.session, share_id)

    async def list_conversations(self, **kwargs):
        from note_admin import social

        return await social.list_conversations(self.session, **kwargs)

    async def block_reset_user(self, user_id: UUID):
        from note_admin import social

        return await social.block_reset_user(self.session, user_id)

    async def list_devices(self, **kwargs):
        from note_admin import sync_devices

        return await sync_devices.list_devices(self.session, **kwargs)

    async def list_user_sessions(self, user_id: UUID, **kwargs):
        from note_admin import sync_devices

        return await sync_devices.list_user_sessions(self.session, user_id, **kwargs)

    async def user_sync_summary(self, user_id: UUID):
        from note_admin import sync_devices

        return await sync_devices.user_sync_summary(self.session, user_id)

    async def force_hydrate_user(self, user_id: UUID):
        from note_admin import sync_devices

        return await sync_devices.force_hydrate_user(self.session, user_id)

    async def list_audit_logs(self, **kwargs):
        from note_admin import sync_devices

        return await sync_devices.list_audit_logs(self.session, **kwargs)

    async def export_user_data(self, user_id: UUID) -> dict:
        from note_memory.service import MemoryService

        return await MemoryService(self.session).export_user_data(user_id)
