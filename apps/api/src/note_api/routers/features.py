from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from note_api.deps import get_current_user_id, get_db, get_device_id
from note_ai.service import AIService
from note_categorization.service import (
    AssignCategoryRequest,
    CategorizationService,
    CategoryCreateRequest,
    CategoryResponse,
)
from note_helpers.service import HelperActionRequest, HelpersService, UpcomingItem
from note_search.service import AIQueryResponse, SearchResult, SearchService
from note_search.smart_search import SmartSearchResponse
from note_search.topic_clusters import TopicClusterResponse, TopicClusterService

router = APIRouter(tags=["features"])


@router.post("/ai/retry/{memory_id}")
async def retry_ai(
    memory_id: UUID,
    user_id: UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    try:
        await AIService(db).retry_memory(user_id, memory_id)
        await db.commit()
        return {"ok": True}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.patch("/helpers/{helper_id}")
async def update_helper(
    helper_id: UUID,
    req: HelperActionRequest,
    user_id: UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    try:
        await HelpersService(db).update_helper_status(user_id, helper_id, req.status)
        return {"ok": True}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/helpers/upcoming", response_model=list[UpcomingItem])
async def upcoming_helpers(
    user_id: UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    return await HelpersService(db).list_upcoming(user_id)


@router.get("/categories", response_model=list[CategoryResponse])
async def list_categories(
    user_id: UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    return await CategorizationService(db).list_categories(user_id)


@router.post("/categories", response_model=CategoryResponse)
async def create_category(
    req: CategoryCreateRequest,
    user_id: UUID = Depends(get_current_user_id),
    device_id: str = Depends(get_device_id),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await CategorizationService(db).create_category(user_id, req, device_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/memories/{memory_id}/categories")
async def assign_category(
    memory_id: UUID,
    req: AssignCategoryRequest,
    user_id: UUID = Depends(get_current_user_id),
    device_id: str = Depends(get_device_id),
    db: AsyncSession = Depends(get_db),
):
    try:
        await CategorizationService(db).assign_to_memory(
            user_id, memory_id, req.category_id, device_id
        )
        return {"ok": True}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


class SearchQuery(BaseModel):
    q: str
    limit: int = 20


class AIQueryRequest(BaseModel):
    query: str


@router.post("/search/keyword", response_model=list[SearchResult])
async def keyword_search(
    req: SearchQuery,
    user_id: UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    return await SearchService(db).keyword_search(user_id, req.q, req.limit)


@router.post("/search/entity", response_model=list[SearchResult])
async def entity_search(
    req: SearchQuery,
    user_id: UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    return await SearchService(db).entity_search(user_id, req.q, req.limit)


@router.post("/search/semantic", response_model=list[SearchResult])
async def semantic_search(
    req: SearchQuery,
    user_id: UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    return await SearchService(db).semantic_search(user_id, req.q, req.limit)


@router.post("/search/ai", response_model=AIQueryResponse)
async def ai_query(
    req: AIQueryRequest,
    user_id: UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    return await SearchService(db).ai_query(user_id, req.query)


@router.post("/search/smart", response_model=SmartSearchResponse)
async def smart_search(
    req: AIQueryRequest,
    user_id: UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    return await SearchService(db).smart_search(user_id, req.query)


@router.get("/topics/clusters", response_model=TopicClusterResponse)
async def topic_clusters(
    user_id: UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    return await TopicClusterService(db).list_clusters(user_id)
