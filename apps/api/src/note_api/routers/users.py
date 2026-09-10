from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.ext.asyncio import AsyncSession

from note_api.deps import get_current_user_id, get_db, get_device_id, get_device_id
from note_memory.service import MemoryService
from note_users.service import ProfileResponse, ProfileUpdateRequest, UsersService, DeviceResponse

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/me", response_model=ProfileResponse)
async def get_profile(user_id: UUID = Depends(get_current_user_id), db: AsyncSession = Depends(get_db)):
    try:
        return await UsersService(db).get_profile(user_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.patch("/me", response_model=ProfileResponse)
async def update_profile(
    req: ProfileUpdateRequest,
    user_id: UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    try:
        profile = await UsersService(db).update_profile(user_id, req)
        await db.commit()
        return profile
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/me/devices", response_model=list[DeviceResponse])
async def list_devices(user_id: UUID = Depends(get_current_user_id), db: AsyncSession = Depends(get_db)):
    devices = await UsersService(db).list_devices(user_id)
    await db.commit()
    return devices


@router.delete("/me/devices/{device_id}")
async def revoke_device(
    device_id: UUID,
    user_id: UUID = Depends(get_current_user_id),
    caller_device_id: str = Depends(get_device_id),
    db: AsyncSession = Depends(get_db),
):
    caller_uuid: UUID | None = None
    if caller_device_id != "unknown":
        try:
            caller_uuid = UUID(caller_device_id)
        except ValueError:
            caller_uuid = None
    try:
        await UsersService(db).revoke_device_session(
            user_id, device_id, caller_device_id=caller_uuid
        )
        await db.commit()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True}


@router.get("/me/export")
async def export_data(user_id: UUID = Depends(get_current_user_id), db: AsyncSession = Depends(get_db)):
    data = await MemoryService(db).export_user_data(user_id)
    return data


@router.delete("/me")
async def delete_account(user_id: UUID = Depends(get_current_user_id), db: AsyncSession = Depends(get_db)):
    await UsersService(db).delete_account(user_id)
    return {"ok": True, "message": "Account scheduled for deletion"}
