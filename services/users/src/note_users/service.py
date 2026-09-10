from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from note_db.models import Device, Session, User


class ProfileResponse(BaseModel):
    id: UUID
    email: str | None
    username: str | None
    display_name: str | None
    avatar_url: str | None
    ai_enabled: bool
    ai_use_notes_context: bool
    helper_auto_accept: bool
    notifications_enabled: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class ProfileUpdateRequest(BaseModel):
    username: str | None = Field(default=None, min_length=3, max_length=64)
    display_name: str | None = Field(default=None, max_length=128)
    avatar_url: str | None = Field(default=None, max_length=2048)
    ai_enabled: bool | None = None
    ai_use_notes_context: bool | None = None
    helper_auto_accept: bool | None = None
    notifications_enabled: bool | None = None


class DeviceResponse(BaseModel):
    id: UUID
    name: str
    platform: str | None
    last_seen_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


class UsersService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_profile(self, user_id: UUID) -> ProfileResponse:
        user = await self.session.get(User, user_id)
        if not user:
            raise ValueError("User not found")
        return ProfileResponse.model_validate(user)

    async def update_profile(self, user_id: UUID, req: ProfileUpdateRequest) -> ProfileResponse:
        user = await self.session.get(User, user_id)
        if not user:
            raise ValueError("User not found")

        if req.username is not None:
            existing = await self.session.scalar(
                select(User).where(User.username == req.username, User.id != user_id)
            )
            if existing:
                raise ValueError("Username taken")
            user.username = req.username

        for field in (
            "display_name", "avatar_url", "ai_enabled", "ai_use_notes_context",
            "helper_auto_accept", "notifications_enabled",
        ):
            value = getattr(req, field)
            if value is not None:
                setattr(user, field, value)

        await self.session.flush()
        return ProfileResponse.model_validate(user)

    async def list_devices(self, user_id: UUID) -> list[DeviceResponse]:
        stmt = (
            select(Device)
            .join(Session, Session.device_id == Device.id)
            .where(
                Device.user_id == user_id,
                Session.user_id == user_id,
                Session.revoked_at.is_(None),
            )
            .distinct()
            .order_by(desc(Device.last_seen_at).nulls_last(), desc(Device.created_at))
        )
        devices = (await self.session.scalars(stmt)).all()
        return [DeviceResponse.model_validate(d) for d in devices]

    async def revoke_device_session(
        self, user_id: UUID, device_id: UUID, *, caller_device_id: UUID | None = None
    ) -> None:
        if caller_device_id is not None and device_id == caller_device_id:
            raise ValueError("Cannot revoke the current device")
        stmt = select(Session).where(
            Session.user_id == user_id,
            Session.device_id == device_id,
            Session.revoked_at.is_(None),
        )
        sessions = (await self.session.scalars(stmt)).all()
        from datetime import UTC

        for s in sessions:
            s.revoked_at = datetime.now(UTC)

    async def delete_account(self, user_id: UUID) -> None:
        from datetime import UTC

        from note_db.models import Lifecycle, MemoryObject

        user = await self.session.get(User, user_id)
        if not user:
            raise ValueError("User not found")
        now = datetime.now(UTC)
        user.deleted_at = now

        stmt = select(Session).where(Session.user_id == user_id, Session.revoked_at.is_(None))
        for s in await self.session.scalars(stmt):
            s.revoked_at = now

        mem_stmt = select(MemoryObject).where(
            MemoryObject.user_id == user_id,
            MemoryObject.lifecycle != Lifecycle.deleted,
        )
        for mem in await self.session.scalars(mem_stmt):
            mem.lifecycle = Lifecycle.deleted
            mem.deleted_at = now
