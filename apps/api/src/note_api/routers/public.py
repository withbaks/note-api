from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from note_api.deps import get_current_user_id, get_db
from note_core.config import get_settings
from note_social.service import PublicLinkResponse, PublicNoteResponse, PublicShareService

router = APIRouter(tags=["public"])


@router.get("/public/notes/{token}", response_model=PublicNoteResponse)
async def get_public_note(
    token: str,
    db: AsyncSession = Depends(get_db),
):
    try:
        return await PublicShareService(db, get_settings().web_base_url).get_public_note(token)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/memories/{memory_id}/public-link", response_model=PublicLinkResponse)
async def create_public_link(
    memory_id: UUID,
    user_id: UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    try:
        result = await PublicShareService(db, get_settings().web_base_url).create_or_get_link(
            user_id, memory_id
        )
        await db.commit()
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/memories/{memory_id}/public-link")
async def revoke_public_link(
    memory_id: UUID,
    user_id: UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    try:
        await PublicShareService(db, get_settings().web_base_url).revoke_link(user_id, memory_id)
        await db.commit()
        return {"ok": True}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
