from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from note_api.deps import get_current_user_id, get_db
from note_db.models import Lifecycle
from note_memory.service import MemoryObjectResponse, MemoryService, MemoryVersionResponse, UnderstandingResponse, HelperResponse, MemoryFactResponse

router = APIRouter(prefix="/memories", tags=["memories"])


@router.get("", response_model=list[MemoryObjectResponse])
async def list_memories(
    lifecycle: Lifecycle | None = None,
    limit: int = Query(default=100, le=500),
    offset: int = 0,
    user_id: UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    return await MemoryService(db).list_memories(user_id, lifecycle, limit, offset)


@router.get("/{memory_id}", response_model=MemoryObjectResponse)
async def get_memory(
    memory_id: UUID,
    user_id: UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await MemoryService(db).get_memory(user_id, memory_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{memory_id}/versions", response_model=list[MemoryVersionResponse])
async def get_versions(
    memory_id: UUID,
    user_id: UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await MemoryService(db).get_versions(user_id, memory_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{memory_id}/understandings", response_model=list[UnderstandingResponse])
async def get_understandings(
    memory_id: UUID,
    user_id: UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await MemoryService(db).get_understandings(user_id, memory_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{memory_id}/helpers", response_model=list[HelperResponse])
async def get_helpers(
    memory_id: UUID,
    user_id: UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await MemoryService(db).get_helpers(user_id, memory_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/facts/all", response_model=list[MemoryFactResponse])
async def list_facts(
    user_id: UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    return await MemoryService(db).list_facts(user_id)
