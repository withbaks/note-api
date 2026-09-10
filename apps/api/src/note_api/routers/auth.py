from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from note_api.deps import get_current_user_id, get_db
from note_auth.service import (
    AppleSignInRequest,
    AuthService,
    LoginRequest,
    RegisterRequest,
    TokenResponse,
)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=TokenResponse)
async def register(req: RegisterRequest, db: AsyncSession = Depends(get_db)):
    try:
        return await AuthService(db).register(req)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/login", response_model=TokenResponse)
async def login(req: LoginRequest, db: AsyncSession = Depends(get_db)):
    try:
        return await AuthService(db).login(req)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


@router.post("/apple", response_model=TokenResponse)
async def apple_sign_in(req: AppleSignInRequest, db: AsyncSession = Depends(get_db)):
    try:
        return await AuthService(db).apple_sign_in(req)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


class RefreshRequest(BaseModel):
    refresh_token: str


@router.post("/refresh", response_model=TokenResponse)
async def refresh(req: RefreshRequest, db: AsyncSession = Depends(get_db)):
    try:
        return await AuthService(db).refresh(req.refresh_token)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


@router.delete("/sessions/{session_id}")
async def revoke_session(
    session_id: UUID,
    user_id: UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    try:
        await AuthService(db).revoke_session(session_id, user_id)
        return {"ok": True}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
