import hashlib
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import bcrypt
from jose import JWTError, jwt

from note_core.config import get_settings

ALGORITHM = "HS256"


def _password_digest(password: str) -> bytes:
    """SHA-256 digest avoids bcrypt's 72-byte input limit."""
    return hashlib.sha256(password.encode("utf-8")).digest()


def hash_password(password: str) -> str:
    return bcrypt.hashpw(_password_digest(password), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(_password_digest(plain), hashed.encode("utf-8"))
    except ValueError:
        return False


def create_access_token(subject: str, extra: dict[str, Any] | None = None) -> str:
    settings = get_settings()
    expire = datetime.now(UTC) + timedelta(minutes=settings.jwt_access_expire_minutes)
    payload = {"sub": subject, "exp": expire, "type": "access"}
    if extra:
        payload.update(extra)
    return jwt.encode(payload, settings.jwt_secret, algorithm=ALGORITHM)


def create_refresh_token(subject: str, device_id: str) -> str:
    settings = get_settings()
    expire = datetime.now(UTC) + timedelta(days=settings.jwt_refresh_expire_days)
    payload = {"sub": subject, "exp": expire, "type": "refresh", "device_id": device_id}
    return jwt.encode(payload, settings.jwt_secret, algorithm=ALGORITHM)


def decode_token(token: str) -> dict[str, Any]:
    settings = get_settings()
    return jwt.decode(token, settings.jwt_secret, algorithms=[ALGORITHM])


def hash_token(token: str) -> str:
    return bcrypt.hashpw(_password_digest(token), bcrypt.gensalt()).decode("utf-8")


def verify_token_hash(token: str, token_hash: str) -> bool:
    try:
        return bcrypt.checkpw(_password_digest(token), token_hash.encode("utf-8"))
    except ValueError:
        return False


def safe_uuid(value: str) -> UUID:
    return UUID(value)
