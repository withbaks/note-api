"""Notification service — pending helpers + push token registry."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from pydantic import BaseModel, Field
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from note_db.models import Helper, HelperStatus, MemoryObject, PushToken, User


class PendingNotification(BaseModel):
    helper_id: UUID
    memory_object_id: UUID
    key: str
    value: str | None
    person: str | None
    scheduled_at: datetime | None
    is_reminder: bool
    is_birthday: bool
    is_calendar: bool
    content_preview: str | None


class PushTokenRequest(BaseModel):
    device_id: str = Field(min_length=1, max_length=128)
    token: str = Field(min_length=1, max_length=512)
    platform: str = Field(min_length=1, max_length=32)


class PushTokenResponse(BaseModel):
    id: UUID
    user_id: UUID
    device_id: str
    token: str
    platform: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class NotificationsService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_pending_notifications(self, user_id: UUID) -> list[PendingNotification]:
        user = await self.session.get(User, user_id)
        if user and not user.notifications_enabled:
            return []

        now = datetime.now(UTC)
        horizon = now + timedelta(days=7)
        reminder_keys = ("is_reminder", "is_birthday", "is_calendar")

        stmt = (
            select(Helper, MemoryObject)
            .join(MemoryObject, Helper.memory_object_id == MemoryObject.id)
            .where(
                MemoryObject.user_id == user_id,
                Helper.status == HelperStatus.accepted,
                Helper.key.in_(reminder_keys),
                or_(
                    and_(
                        Helper.scheduled_at.is_not(None),
                        Helper.scheduled_at >= now,
                        Helper.scheduled_at <= horizon,
                    ),
                    and_(Helper.key == "is_reminder", Helper.scheduled_at.is_(None)),
                ),
            )
            .order_by(Helper.scheduled_at.asc().nullsfirst())
        )
        rows = (await self.session.execute(stmt)).all()
        items: list[PendingNotification] = []
        for helper, mem in rows:
            preview = mem.content_text or (
                f"{mem.structured_title}: {mem.structured_value}" if mem.structured_title else None
            )
            items.append(
                PendingNotification(
                    helper_id=helper.id,
                    memory_object_id=mem.id,
                    key=helper.key,
                    value=helper.value,
                    person=helper.person,
                    scheduled_at=helper.scheduled_at,
                    is_reminder=helper.key == "is_reminder",
                    is_birthday=helper.key == "is_birthday",
                    is_calendar=helper.key == "is_calendar",
                    content_preview=preview[:100] if preview else None,
                )
            )
        return items

    async def register_push_token(self, user_id: UUID, req: PushTokenRequest) -> PushTokenResponse:
        existing = await self.session.scalar(
            select(PushToken).where(
                PushToken.user_id == user_id,
                PushToken.device_id == req.device_id,
            )
        )
        if existing:
            existing.token = req.token
            existing.platform = req.platform
            existing.updated_at = datetime.now(UTC)
            await self.session.flush()
            return PushTokenResponse.model_validate(existing)

        token = PushToken(
            user_id=user_id,
            device_id=req.device_id,
            token=req.token,
            platform=req.platform,
        )
        self.session.add(token)
        await self.session.flush()
        return PushTokenResponse.model_validate(token)

    async def list_push_tokens(self, user_id: UUID) -> list[PushTokenResponse]:
        stmt = select(PushToken).where(PushToken.user_id == user_id).order_by(PushToken.updated_at.desc())
        tokens = (await self.session.scalars(stmt)).all()
        return [PushTokenResponse.model_validate(t) for t in tokens]

    async def mark_delivered(self, user_id: UUID, notification_id: UUID) -> dict:
        # Stub: client-driven delivery ack until push provider is wired.
        return {"ok": True, "notification_id": str(notification_id), "delivered": True}
