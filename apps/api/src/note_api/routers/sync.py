from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from note_api.deps import get_current_user_id, get_db
from note_sync.service import HydrateResponse, PullResponse, PushRequest, PushResponse, SyncService

router = APIRouter(prefix="/sync", tags=["sync"])


@router.post("/push", response_model=PushResponse)
async def push(
    req: PushRequest,
    user_id: UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    result = await SyncService(db).push(user_id, req)
    await db.commit()

    newly = set(result.newly_applied)

    # Wake other devices for any successful new apply (not only AI)
    if newly:
        try:
            from note_queue import get_redis_pool, publish_sync_event

            pool = await get_redis_pool()
            # One wake is enough — clients pull the full mutation log
            first = next(m for m in req.mutations if m.id in newly)
            await publish_sync_event(
                pool,
                str(user_id),
                {
                    "type": "sync_update",
                    "entity_type": first.entity_type,
                    "entity_id": str(first.entity_id),
                },
            )
        except Exception:
            pass

    # Enqueue AI only for newly applied memory creates (idempotent retries skip)
    try:
        from note_queue import enqueue_ai_job, get_redis_pool

        pool = await get_redis_pool()
        for mutation in req.mutations:
            if (
                mutation.id in newly
                and mutation.entity_type == "memory_object"
                and mutation.operation == "create"
            ):
                await enqueue_ai_job(pool, str(mutation.entity_id))
    except Exception:
        pass

    return result


@router.get("/pull", response_model=PullResponse)
async def pull(
    device_id: UUID = Query(...),
    cursor: str = Query(default="0"),
    user_id: UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    return await SyncService(db).pull(user_id, device_id, cursor)


@router.post("/hydrate", response_model=HydrateResponse)
async def hydrate(
    device_id: UUID = Query(...),
    user_id: UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    return await SyncService(db).hydrate(user_id, device_id)
