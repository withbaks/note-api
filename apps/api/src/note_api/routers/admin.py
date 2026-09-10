from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from note_admin.audit import write_audit_log
from note_admin.permissions import (
    PERM_AUDIT_READ,
    PERM_EXPORT_WRITE,
    PERM_HELPERS_WRITE,
    PERM_MEMORIES_DELETE,
    PERM_MEMORIES_READ,
    PERM_MEMORIES_WRITE,
    PERM_PIPELINE_ADMIN,
    PERM_PIPELINE_WRITE,
    PERM_SOCIAL_ADMIN,
    PERM_SOCIAL_READ,
    PERM_USERS_ADMIN,
    PERM_USERS_DELETE,
    PERM_USERS_READ,
    PERM_USERS_WRITE,
    effective_admin_role,
)
from note_admin.service import (
    AdminMemoryDetail,
    AdminMemoryListItem,
    AdminService,
    AdminUserDetail,
    AdminUserListItem,
    AdminUserUpdate,
    OverviewResponse,
)
from note_api.deps import get_db, get_current_staff_user, require_admin_permission
from note_db.models import Lifecycle, User

router = APIRouter(prefix="/admin", tags=["admin"])


class HelperStatusUpdate(BaseModel):
    status: str


@router.get("/me")
async def admin_me(staff: User = Depends(get_current_staff_user)):
    return {
        "id": str(staff.id),
        "email": staff.email,
        "username": staff.username,
        "display_name": staff.display_name,
        "is_staff": staff.is_staff,
        "admin_role": effective_admin_role(staff).value,
    }


@router.get("/overview", response_model=OverviewResponse)
async def overview(
    _: User = Depends(require_admin_permission(PERM_USERS_READ)),
    db: AsyncSession = Depends(get_db),
):
    return await AdminService(db).overview()


@router.get("/health")
async def health(
    _: User = Depends(get_current_staff_user),
    db: AsyncSession = Depends(get_db),
):
    return await AdminService(db).health_check()


@router.get("/overview/trends")
async def overview_trends(
    days: int = Query(7, ge=1, le=90),
    _: User = Depends(require_admin_permission(PERM_USERS_READ)),
    db: AsyncSession = Depends(get_db),
):
    return await AdminService(db).overview_trends(days=days)


@router.get("/metrics")
async def metrics(
    _: User = Depends(require_admin_permission(PERM_USERS_READ)),
    db: AsyncSession = Depends(get_db),
):
    return await AdminService(db).metrics_snapshot()


@router.get("/audit-logs")
async def audit_logs(
    actor_id: UUID | None = None,
    action: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    _: User = Depends(require_admin_permission(PERM_AUDIT_READ)),
    db: AsyncSession = Depends(get_db),
):
    items, total = await AdminService(db).list_audit_logs(
        actor_id=actor_id, action=action, limit=limit, offset=offset
    )
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@router.get("/search")
async def search(
    q: str = Query(min_length=1),
    limit: int = Query(5, ge=1, le=20),
    _: User = Depends(require_admin_permission(PERM_USERS_READ)),
    db: AsyncSession = Depends(get_db),
):
    return await AdminService(db).global_search(q, limit=limit)


@router.get("/users")
async def list_users(
    q: str | None = None,
    staff_only: bool = False,
    include_deleted: bool = True,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    _: User = Depends(require_admin_permission(PERM_USERS_READ)),
    db: AsyncSession = Depends(get_db),
) -> dict:
    items, total = await AdminService(db).list_users(
        q=q,
        staff_only=staff_only,
        include_deleted=include_deleted,
        limit=limit,
        offset=offset,
    )
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@router.get("/users/{user_id}", response_model=AdminUserDetail)
async def get_user(
    user_id: UUID,
    _: User = Depends(require_admin_permission(PERM_USERS_READ)),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await AdminService(db).get_user(user_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.patch("/users/{user_id}", response_model=AdminUserDetail)
async def update_user(
    user_id: UUID,
    req: AdminUserUpdate,
    staff: User = Depends(require_admin_permission(PERM_USERS_WRITE)),
    db: AsyncSession = Depends(get_db),
):
    if req.is_staff is not None or req.admin_role is not None:
        from note_admin.permissions import has_permission

        if not has_permission(staff, PERM_USERS_ADMIN):
            raise HTTPException(status_code=403, detail="Missing permission users:admin")
    if user_id == staff.id and req.is_staff is False:
        raise HTTPException(status_code=400, detail="Cannot remove your own staff flag")
    try:
        result = await AdminService(db).update_user(user_id, req)
        await write_audit_log(
            db,
            actor_id=staff.id,
            action="user.update",
            target_type="user",
            target_id=user_id,
            metadata=req.model_dump(exclude_none=True),
        )
        await db.commit()
        return result
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/users/{user_id}/soft-delete")
async def soft_delete_user(
    user_id: UUID,
    staff: User = Depends(require_admin_permission(PERM_USERS_DELETE)),
    db: AsyncSession = Depends(get_db),
):
    if user_id == staff.id:
        raise HTTPException(status_code=400, detail="Cannot delete your own account from admin")
    try:
        await AdminService(db).soft_delete_user(user_id)
        await write_audit_log(
            db,
            actor_id=staff.id,
            action="user.soft_delete",
            target_type="user",
            target_id=user_id,
        )
        await db.commit()
        return {"ok": True}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/users/{user_id}/revoke-sessions")
async def revoke_sessions(
    user_id: UUID,
    staff: User = Depends(require_admin_permission(PERM_USERS_WRITE)),
    db: AsyncSession = Depends(get_db),
):
    try:
        count = await AdminService(db).revoke_user_sessions(user_id)
        await write_audit_log(
            db,
            actor_id=staff.id,
            action="user.revoke_sessions",
            target_type="user",
            target_id=user_id,
            metadata={"revoked": count},
        )
        await db.commit()
        return {"ok": True, "revoked": count}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/users/{user_id}/sessions")
async def user_sessions(
    user_id: UUID,
    active_only: bool = True,
    _: User = Depends(require_admin_permission(PERM_USERS_READ)),
    db: AsyncSession = Depends(get_db),
):
    return await AdminService(db).list_user_sessions(user_id, active_only=active_only)


@router.get("/users/{user_id}/sync")
async def user_sync(
    user_id: UUID,
    _: User = Depends(require_admin_permission(PERM_USERS_READ)),
    db: AsyncSession = Depends(get_db),
):
    return await AdminService(db).user_sync_summary(user_id)


@router.post("/users/{user_id}/force-hydrate-flag")
async def force_hydrate(
    user_id: UUID,
    staff: User = Depends(require_admin_permission(PERM_USERS_WRITE)),
    db: AsyncSession = Depends(get_db),
):
    result = await AdminService(db).force_hydrate_user(user_id)
    await write_audit_log(
        db,
        actor_id=staff.id,
        action="user.force_hydrate",
        target_type="user",
        target_id=user_id,
        metadata=result,
    )
    await db.commit()
    return result


@router.get("/users/{user_id}/export")
async def export_user(
    user_id: UUID,
    staff: User = Depends(require_admin_permission(PERM_EXPORT_WRITE)),
    db: AsyncSession = Depends(get_db),
):
    try:
        data = await AdminService(db).export_user_data(user_id)
        await write_audit_log(
            db,
            actor_id=staff.id,
            action="user.export",
            target_type="user",
            target_id=user_id,
        )
        await db.commit()
        return data
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/users/{user_id}/block-reset")
async def block_reset(
    user_id: UUID,
    staff: User = Depends(require_admin_permission(PERM_SOCIAL_ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    result = await AdminService(db).block_reset_user(user_id)
    await write_audit_log(
        db,
        actor_id=staff.id,
        action="user.block_reset",
        target_type="user",
        target_id=user_id,
        metadata=result,
    )
    await db.commit()
    return result


@router.get("/users/{user_id}/helpers")
async def user_helpers(
    user_id: UUID,
    status: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    _: User = Depends(require_admin_permission(PERM_USERS_READ)),
    db: AsyncSession = Depends(get_db),
):
    items, total = await AdminService(db).list_user_helpers(
        user_id, status=status, limit=limit, offset=offset
    )
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@router.get("/users/{user_id}/facts")
async def user_facts(
    user_id: UUID,
    limit: int = Query(100, ge=1, le=200),
    offset: int = Query(0, ge=0),
    _: User = Depends(require_admin_permission(PERM_USERS_READ)),
    db: AsyncSession = Depends(get_db),
):
    items, total = await AdminService(db).list_user_facts(user_id, limit=limit, offset=offset)
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@router.get("/users/{user_id}/people")
async def user_people(
    user_id: UUID,
    limit: int = Query(100, ge=1, le=200),
    offset: int = Query(0, ge=0),
    _: User = Depends(require_admin_permission(PERM_USERS_READ)),
    db: AsyncSession = Depends(get_db),
):
    items, total = await AdminService(db).list_user_people(user_id, limit=limit, offset=offset)
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@router.get("/users/{user_id}/categories")
async def user_categories(
    user_id: UUID,
    limit: int = Query(100, ge=1, le=200),
    offset: int = Query(0, ge=0),
    _: User = Depends(require_admin_permission(PERM_USERS_READ)),
    db: AsyncSession = Depends(get_db),
):
    items, total = await AdminService(db).list_user_categories(user_id, limit=limit, offset=offset)
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@router.get("/memories")
async def list_memories(
    q: str | None = None,
    user_id: UUID | None = None,
    lifecycle: str | None = None,
    ai_state: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    _: User = Depends(require_admin_permission(PERM_MEMORIES_READ)),
    db: AsyncSession = Depends(get_db),
) -> dict:
    try:
        items, total = await AdminService(db).list_memories(
            q=q,
            user_id=user_id,
            lifecycle=lifecycle,
            ai_state=ai_state,
            limit=limit,
            offset=offset,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@router.get("/memories/{memory_id}", response_model=AdminMemoryDetail)
async def get_memory(
    memory_id: UUID,
    _: User = Depends(require_admin_permission(PERM_MEMORIES_READ)),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await AdminService(db).get_memory(memory_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/memories/{memory_id}/soft-delete", response_model=AdminMemoryDetail)
async def soft_delete_memory(
    memory_id: UUID,
    staff: User = Depends(require_admin_permission(PERM_MEMORIES_DELETE)),
    db: AsyncSession = Depends(get_db),
):
    try:
        result = await AdminService(db).set_memory_lifecycle(memory_id, Lifecycle.deleted)
        await write_audit_log(
            db,
            actor_id=staff.id,
            action="memory.soft_delete",
            target_type="memory",
            target_id=memory_id,
        )
        await db.commit()
        return result
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/memories/{memory_id}/restore", response_model=AdminMemoryDetail)
async def restore_memory(
    memory_id: UUID,
    staff: User = Depends(require_admin_permission(PERM_MEMORIES_WRITE)),
    db: AsyncSession = Depends(get_db),
):
    try:
        result = await AdminService(db).set_memory_lifecycle(memory_id, Lifecycle.active)
        await write_audit_log(
            db,
            actor_id=staff.id,
            action="memory.restore",
            target_type="memory",
            target_id=memory_id,
        )
        await db.commit()
        return result
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/memories/{memory_id}/retry-ai")
async def retry_ai(
    memory_id: UUID,
    staff: User = Depends(require_admin_permission(PERM_PIPELINE_WRITE)),
    db: AsyncSession = Depends(get_db),
):
    from note_ai.service import AIService
    from note_db.models import MemoryObject

    obj = await db.get(MemoryObject, memory_id)
    if not obj:
        raise HTTPException(status_code=404, detail="Memory not found")
    try:
        await AIService(db).retry_memory(obj.user_id, memory_id)
        await write_audit_log(
            db,
            actor_id=staff.id,
            action="memory.retry_ai",
            target_type="memory",
            target_id=memory_id,
        )
        await db.commit()
        return {"ok": True, "memory_id": str(memory_id)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/ai-jobs")
async def list_ai_jobs(
    state: str | None = None,
    user_id: UUID | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    _: User = Depends(require_admin_permission(PERM_MEMORIES_READ)),
    db: AsyncSession = Depends(get_db),
):
    items, total = await AdminService(db).list_ai_jobs(
        state=state, user_id=user_id, limit=limit, offset=offset
    )
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@router.post("/ai-jobs/{job_id}/retry")
async def retry_ai_job(
    job_id: UUID,
    staff: User = Depends(require_admin_permission(PERM_PIPELINE_WRITE)),
    db: AsyncSession = Depends(get_db),
):
    try:
        result = await AdminService(db).retry_ai_job(job_id)
        await write_audit_log(
            db,
            actor_id=staff.id,
            action="ai_job.retry",
            target_type="ai_job",
            target_id=job_id,
        )
        await db.commit()
        return result
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/ai-jobs/retry-failed")
async def retry_failed_ai_jobs(
    limit: int = Query(50, ge=1, le=200),
    staff: User = Depends(require_admin_permission(PERM_PIPELINE_ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    result = await AdminService(db).retry_failed_ai_jobs(limit=limit)
    await write_audit_log(
        db,
        actor_id=staff.id,
        action="ai_job.retry_failed_bulk",
        metadata=result,
    )
    await db.commit()
    return result


@router.get("/ingest")
async def list_ingest(
    status: str | None = None,
    user_id: UUID | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    _: User = Depends(require_admin_permission(PERM_MEMORIES_READ)),
    db: AsyncSession = Depends(get_db),
):
    items, total = await AdminService(db).list_ingest_items(
        status=status, user_id=user_id, limit=limit, offset=offset
    )
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@router.post("/ingest/{item_id}/retry")
async def retry_ingest(
    item_id: UUID,
    staff: User = Depends(require_admin_permission(PERM_PIPELINE_WRITE)),
    db: AsyncSession = Depends(get_db),
):
    try:
        result = await AdminService(db).retry_ingest_item(item_id)
        await write_audit_log(
            db,
            actor_id=staff.id,
            action="ingest.retry",
            target_type="ingest_item",
            target_id=item_id,
        )
        await db.commit()
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/helpers")
async def list_helpers(
    q: str | None = None,
    status: str | None = None,
    source: str | None = None,
    kind: str | None = None,
    user_id: UUID | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    _: User = Depends(require_admin_permission(PERM_MEMORIES_READ)),
    db: AsyncSession = Depends(get_db),
):
    items, total = await AdminService(db).list_helpers(
        q=q, status=status, source=source, kind=kind, user_id=user_id, limit=limit, offset=offset
    )
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@router.get("/helpers/{helper_id}")
async def get_helper(
    helper_id: UUID,
    _: User = Depends(require_admin_permission(PERM_MEMORIES_READ)),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await AdminService(db).get_helper(helper_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.patch("/helpers/{helper_id}")
async def patch_helper(
    helper_id: UUID,
    req: HelperStatusUpdate,
    staff: User = Depends(require_admin_permission(PERM_HELPERS_WRITE)),
    db: AsyncSession = Depends(get_db),
):
    try:
        result = await AdminService(db).update_helper_status(helper_id, req.status)
        await write_audit_log(
            db,
            actor_id=staff.id,
            action="helper.update_status",
            target_type="helper",
            target_id=helper_id,
            metadata={"status": req.status},
        )
        await db.commit()
        return result
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/facts")
async def list_facts(
    q: str | None = None,
    user_id: UUID | None = None,
    person_id: UUID | None = None,
    semantic_type: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    _: User = Depends(require_admin_permission(PERM_MEMORIES_READ)),
    db: AsyncSession = Depends(get_db),
):
    items, total = await AdminService(db).list_facts(
        q=q,
        user_id=user_id,
        person_id=person_id,
        semantic_type=semantic_type,
        limit=limit,
        offset=offset,
    )
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@router.get("/people")
async def list_people(
    q: str | None = None,
    user_id: UUID | None = None,
    has_merge: bool | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    _: User = Depends(require_admin_permission(PERM_MEMORIES_READ)),
    db: AsyncSession = Depends(get_db),
):
    items, total = await AdminService(db).list_people(
        q=q, user_id=user_id, has_merge=has_merge, limit=limit, offset=offset
    )
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@router.get("/categories")
async def list_categories(
    user_id: UUID | None = None,
    source: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    _: User = Depends(require_admin_permission(PERM_MEMORIES_READ)),
    db: AsyncSession = Depends(get_db),
):
    items, total = await AdminService(db).list_categories(
        user_id=user_id, source=source, limit=limit, offset=offset
    )
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@router.get("/friendships")
async def list_friendships(
    status: str | None = None,
    user_id: UUID | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    _: User = Depends(require_admin_permission(PERM_SOCIAL_READ)),
    db: AsyncSession = Depends(get_db),
):
    items, total = await AdminService(db).list_friendships(
        status=status, user_id=user_id, limit=limit, offset=offset
    )
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@router.get("/groups")
async def list_groups(
    q: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    _: User = Depends(require_admin_permission(PERM_SOCIAL_READ)),
    db: AsyncSession = Depends(get_db),
):
    items, total = await AdminService(db).list_groups(q=q, limit=limit, offset=offset)
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@router.get("/groups/{group_id}")
async def get_group(
    group_id: UUID,
    _: User = Depends(require_admin_permission(PERM_SOCIAL_READ)),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await AdminService(db).get_group(group_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/shares")
async def list_shares(
    user_id: UUID | None = None,
    memory_id: UUID | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    _: User = Depends(require_admin_permission(PERM_SOCIAL_READ)),
    db: AsyncSession = Depends(get_db),
):
    items, total = await AdminService(db).list_shares(
        user_id=user_id, memory_id=memory_id, limit=limit, offset=offset
    )
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@router.post("/shares/{share_id}/revoke")
async def revoke_share(
    share_id: UUID,
    staff: User = Depends(require_admin_permission(PERM_SOCIAL_ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    try:
        result = await AdminService(db).revoke_share(share_id)
        await write_audit_log(
            db,
            actor_id=staff.id,
            action="share.revoke",
            target_type="share",
            target_id=share_id,
        )
        await db.commit()
        return result
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/conversations")
async def list_conversations(
    kind: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    _: User = Depends(require_admin_permission(PERM_SOCIAL_READ)),
    db: AsyncSession = Depends(get_db),
):
    items, total = await AdminService(db).list_conversations(
        kind=kind, limit=limit, offset=offset
    )
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@router.get("/devices")
async def list_devices(
    user_id: UUID | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    _: User = Depends(require_admin_permission(PERM_USERS_READ)),
    db: AsyncSession = Depends(get_db),
):
    items, total = await AdminService(db).list_devices(
        user_id=user_id, limit=limit, offset=offset
    )
    return {"items": items, "total": total, "limit": limit, "offset": offset}


_ = (AdminMemoryListItem, AdminUserListItem)
