from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from note_api.deps import get_current_user_id, get_db
from note_notifications.service import (
    NotificationsService,
    PendingNotification,
    PushTokenRequest,
    PushTokenResponse,
)

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("/pending", response_model=list[PendingNotification])
async def pending_notifications(
    user_id: UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    return await NotificationsService(db).get_pending_notifications(user_id)


@router.post("/push-tokens", response_model=PushTokenResponse)
async def register_push_token(
    req: PushTokenRequest,
    user_id: UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    result = await NotificationsService(db).register_push_token(user_id, req)
    await db.commit()
    return result


@router.get("/push-tokens", response_model=list[PushTokenResponse])
async def list_push_tokens(
    user_id: UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    return await NotificationsService(db).list_push_tokens(user_id)


@router.post("/{notification_id}/delivered")
async def mark_delivered(
    notification_id: UUID,
    user_id: UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    return await NotificationsService(db).mark_delivered(user_id, notification_id)
