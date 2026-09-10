from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from uuid import UUID

import httpx
import jwt
from jwt.algorithms import RSAAlgorithm
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from note_core.config import get_settings
from note_core.security import (
    create_access_token,
    create_refresh_token,
    hash_password,
    hash_token,
    verify_password,
)
from note_db.models import Device, Session, User

APPLE_JWKS_URL = "https://appleid.apple.com/auth/keys"
APPLE_ISSUER = "https://appleid.apple.com"


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)
    device_name: str = "Unknown Device"
    platform: str | None = None


class LoginRequest(BaseModel):
    email: EmailStr
    password: str
    device_id: UUID | None = None
    device_name: str = "Unknown Device"
    platform: str | None = None


class AppleSignInRequest(BaseModel):
    identity_token: str
    email: EmailStr | None = None
    display_name: str | None = None
    device_id: UUID | None = None
    device_name: str = "Unknown Device"
    platform: str | None = None
    apple_sub: str | None = None  # DEV only when apple_skip_verify


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user_id: UUID
    device_id: UUID
    session_id: UUID


async def verify_apple_identity_token(identity_token: str) -> str:
    """Verify Apple identity JWT and return the subject (`sub`)."""
    settings = get_settings()
    try:
        header = jwt.get_unverified_header(identity_token)
    except jwt.PyJWTError as exc:
        raise ValueError("Invalid Apple identity token") from exc

    kid = header.get("kid")
    if not kid:
        raise ValueError("Invalid Apple identity token")

    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(APPLE_JWKS_URL)
        response.raise_for_status()
        jwks = response.json()

    matching = next((key for key in jwks.get("keys", []) if key.get("kid") == kid), None)
    if not matching:
        raise ValueError("Apple signing key not found")

    public_key = RSAAlgorithm.from_jwk(json.dumps(matching))
    decode_kwargs: dict = {
        "algorithms": ["RS256"],
        "issuer": APPLE_ISSUER,
    }
    if settings.apple_client_id:
        decode_kwargs["audience"] = settings.apple_client_id
    else:
        decode_kwargs["options"] = {"verify_aud": False}

    try:
        payload = jwt.decode(identity_token, public_key, **decode_kwargs)
    except jwt.PyJWTError as exc:
        raise ValueError("Apple identity token verification failed") from exc

    sub = payload.get("sub")
    if not sub:
        raise ValueError("Apple identity token missing subject")
    return str(sub)


class AuthService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def register(self, req: RegisterRequest) -> TokenResponse:
        existing = await self.session.scalar(select(User).where(User.email == req.email))
        if existing:
            raise ValueError("Email already registered")

        user = User(email=req.email, password_hash=hash_password(req.password))
        device = Device(user=user, name=req.device_name, platform=req.platform)
        self.session.add_all([user, device])
        await self.session.flush()
        from note_db.people import PeopleService

        await PeopleService(self.session).ensure_self(user)
        return await self._create_session(user, device)

    async def login(self, req: LoginRequest) -> TokenResponse:
        user = await self.session.scalar(select(User).where(User.email == req.email))
        if not user or not user.password_hash or not verify_password(req.password, user.password_hash):
            raise ValueError("Invalid credentials")
        if user.deleted_at:
            raise ValueError("Account deleted")

        device = await self._get_or_create_device(user, req.device_id, req.device_name, req.platform)
        return await self._create_session(user, device)

    async def apple_sign_in(self, req: AppleSignInRequest) -> TokenResponse:
        settings = get_settings()
        if settings.apple_skip_verify:
            if not req.apple_sub:
                raise ValueError("apple_sub required when apple_skip_verify is enabled")
            apple_sub = req.apple_sub
        else:
            apple_sub = await verify_apple_identity_token(req.identity_token)

        user = await self.session.scalar(select(User).where(User.apple_sub == apple_sub))
        if not user:
            user = User(
                apple_sub=apple_sub,
                email=req.email,
                display_name=req.display_name,
            )
            self.session.add(user)
            await self.session.flush()
            from note_db.people import PeopleService

            await PeopleService(self.session).ensure_self(user)

        device = await self._get_or_create_device(
            user, req.device_id, req.device_name, req.platform
        )
        return await self._create_session(user, device)

    async def refresh(self, refresh_token: str) -> TokenResponse:
        from note_core.security import decode_token

        try:
            payload = decode_token(refresh_token)
        except Exception as exc:
            raise ValueError("Invalid refresh token") from exc

        if payload.get("type") != "refresh":
            raise ValueError("Invalid token type")

        user_id = UUID(payload["sub"])
        device_id = UUID(payload["device_id"])

        user = await self.session.get(User, user_id)
        if not user or user.deleted_at:
            raise ValueError("User not found")

        device = await self.session.get(Device, device_id)
        if not device:
            raise ValueError("Device not found")

        stmt = select(Session).where(
            Session.user_id == user_id,
            Session.device_id == device_id,
            Session.revoked_at.is_(None),
        )
        db_session = await self.session.scalar(stmt)
        if not db_session:
            raise ValueError("Session not found")

        from note_core.security import verify_token_hash

        if not verify_token_hash(refresh_token, db_session.refresh_token_hash):
            raise ValueError("Invalid refresh token")

        return await self._create_session(user, device, existing_session=db_session)

    async def revoke_session(self, session_id: UUID, user_id: UUID) -> None:
        db_session = await self.session.get(Session, session_id)
        if not db_session or db_session.user_id != user_id:
            raise ValueError("Session not found")
        db_session.revoked_at = datetime.now(UTC)

    async def _get_or_create_device(
        self, user: User, device_id: UUID | None, name: str, platform: str | None
    ) -> Device:
        if device_id:
            device = await self.session.get(Device, device_id)
            if device and device.user_id == user.id:
                device.name = name
                device.last_seen_at = datetime.now(UTC)
                return device

        device = Device(user_id=user.id, name=name, platform=platform, last_seen_at=datetime.now(UTC))
        self.session.add(device)
        await self.session.flush()
        return device

    async def _create_session(
        self, user: User, device: Device, existing_session: Session | None = None
    ) -> TokenResponse:
        device.last_seen_at = datetime.now(UTC)
        refresh = create_refresh_token(str(user.id), str(device.id))
        access = create_access_token(str(user.id), {"device_id": str(device.id)})

        if existing_session:
            existing_session.refresh_token_hash = hash_token(refresh)
            existing_session.expires_at = datetime.now(UTC) + timedelta(days=30)
            session = existing_session
        else:
            session = Session(
                user_id=user.id,
                device_id=device.id,
                refresh_token_hash=hash_token(refresh),
                expires_at=datetime.now(UTC) + timedelta(days=30),
            )
            self.session.add(session)
            await self.session.flush()

        return TokenResponse(
            access_token=access,
            refresh_token=refresh,
            user_id=user.id,
            device_id=device.id,
            session_id=session.id,
        )
