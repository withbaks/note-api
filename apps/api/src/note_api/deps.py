from collections.abc import Callable
from uuid import UUID

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError
from sqlalchemy.ext.asyncio import AsyncSession

from note_core.security import decode_token
from note_db.models import User
from note_db.session import get_session

security = HTTPBearer()


async def get_db() -> AsyncSession:
    async for session in get_session():
        yield session


async def get_current_user_id(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> UUID:
    try:
        payload = decode_token(credentials.credentials)
        if payload.get("type") != "access":
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token type")
        return UUID(payload["sub"])
    except (JWTError, ValueError, KeyError) as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token") from exc


async def get_current_staff_user(
    user_id: UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
) -> User:
    user = await db.get(User, user_id)
    if not user or user.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    if not user.is_staff:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Staff access required")
    return user


def require_admin_permission(permission: str) -> Callable:
    """FastAPI dependency factory: staff user must hold the given permission."""

    async def _check(staff: User = Depends(get_current_staff_user)) -> User:
        from note_admin.permissions import has_permission

        if not has_permission(staff, permission):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Missing permission {permission}",
            )
        return staff

    return _check


async def get_device_id(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> str:
    try:
        payload = decode_token(credentials.credentials)
        return payload.get("device_id", "unknown")
    except (JWTError, ValueError):
        return "unknown"
