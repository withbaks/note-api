"""RBAC permissions for the admin portal."""

from __future__ import annotations

from note_db.models import AdminRole, User

# Permission strings used by require_admin_permission()
PERM_USERS_READ = "users:read"
PERM_USERS_WRITE = "users:write"
PERM_USERS_DELETE = "users:delete"
PERM_USERS_ADMIN = "users:admin"
PERM_MEMORIES_READ = "memories:read"
PERM_MEMORIES_WRITE = "memories:write"
PERM_MEMORIES_DELETE = "memories:delete"
PERM_HELPERS_WRITE = "helpers:write"
PERM_PIPELINE_WRITE = "pipeline:write"
PERM_PIPELINE_ADMIN = "pipeline:admin"
PERM_SOCIAL_READ = "social:read"
PERM_SOCIAL_ADMIN = "social:admin"
PERM_AUDIT_READ = "audit:read"
PERM_EXPORT_WRITE = "export:write"

_ALL_READ = {
    PERM_USERS_READ,
    PERM_MEMORIES_READ,
    PERM_SOCIAL_READ,
    PERM_AUDIT_READ,
}

_VIEWER: frozenset[str] = frozenset(_ALL_READ)

_SUPPORT: frozenset[str] = frozenset(
    _ALL_READ
    | {
        PERM_USERS_WRITE,
        PERM_MEMORIES_WRITE,
        PERM_MEMORIES_DELETE,
        PERM_HELPERS_WRITE,
        PERM_PIPELINE_WRITE,
        PERM_EXPORT_WRITE,
    }
)

_ADMIN: frozenset[str] = frozenset(
    _SUPPORT
    | {
        PERM_USERS_DELETE,
        PERM_USERS_ADMIN,
        PERM_PIPELINE_ADMIN,
        PERM_SOCIAL_ADMIN,
    }
)

ROLE_PERMISSIONS: dict[AdminRole, frozenset[str]] = {
    AdminRole.viewer: _VIEWER,
    AdminRole.support: _SUPPORT,
    AdminRole.admin: _ADMIN,
}


def effective_admin_role(user: User) -> AdminRole:
    if user.admin_role is not None:
        return user.admin_role
    return AdminRole.admin if user.is_staff else AdminRole.viewer


def has_permission(user: User, permission: str) -> bool:
    if not user.is_staff:
        return False
    role = effective_admin_role(user)
    return permission in ROLE_PERMISSIONS.get(role, frozenset())


def require_permission(user: User, permission: str) -> None:
    if not has_permission(user, permission):
        from fastapi import HTTPException, status

        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Missing permission {permission}",
        )
