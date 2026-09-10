"""Admin social domain."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from note_db.models import Conversation, ConversationParticipant, Friendship, Group, GroupMember, Share, User


async def list_friendships(
    session: AsyncSession,
    *,
    status: str | None = None,
    user_id: UUID | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[dict], int]:
    filters = []
    if status:
        filters.append(Friendship.status == status)
    if user_id:
        filters.append(Friendship.user_id == user_id)

    total = int(
        await session.scalar(select(func.count()).select_from(Friendship).where(*filters)) or 0
    )
    rows = (
        await session.scalars(
            select(Friendship)
            .where(*filters)
            .order_by(Friendship.created_at.desc())
            .limit(min(limit, 200))
            .offset(max(offset, 0))
        )
    ).all()
    items = []
    for f in rows:
        user = await session.get(User, f.user_id)
        friend = await session.get(User, f.friend_id)
        items.append(
            {
                "id": str(f.id),
                "user_id": str(f.user_id),
                "user_email": user.email if user else None,
                "friend_id": str(f.friend_id),
                "friend_email": friend.email if friend else None,
                "status": f.status,
                "created_at": f.created_at.isoformat(),
            }
        )
    return items, total


async def list_groups(
    session: AsyncSession,
    *,
    q: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[dict], int]:
    filters = []
    if q:
        filters.append(Group.name.ilike(f"%{q.strip()}%"))

    total = int(
        await session.scalar(select(func.count()).select_from(Group).where(*filters)) or 0
    )
    rows = (
        await session.scalars(
            select(Group)
            .where(*filters)
            .order_by(Group.created_at.desc())
            .limit(min(limit, 200))
            .offset(max(offset, 0))
        )
    ).all()
    items = []
    for g in rows:
        member_count = int(
            await session.scalar(
                select(func.count()).select_from(GroupMember).where(GroupMember.group_id == g.id)
            )
            or 0
        )
        owner = await session.get(User, g.owner_id)
        items.append(
            {
                "id": str(g.id),
                "name": g.name,
                "owner_id": str(g.owner_id),
                "owner_email": owner.email if owner else None,
                "member_count": member_count,
                "created_at": g.created_at.isoformat(),
            }
        )
    return items, total


async def get_group(session: AsyncSession, group_id: UUID) -> dict:
    group = await session.get(Group, group_id)
    if not group:
        raise ValueError("Group not found")
    members = (
        await session.scalars(
            select(GroupMember).where(GroupMember.group_id == group_id)
        )
    ).all()
    member_items = []
    for m in members:
        user = await session.get(User, m.user_id)
        member_items.append(
            {
                "user_id": str(m.user_id),
                "email": user.email if user else None,
                "username": user.username if user else None,
                "role": m.role,
            }
        )
    owner = await session.get(User, group.owner_id)
    return {
        "id": str(group.id),
        "name": group.name,
        "owner_id": str(group.owner_id),
        "owner_email": owner.email if owner else None,
        "members": member_items,
        "created_at": group.created_at.isoformat(),
    }


async def list_shares(
    session: AsyncSession,
    *,
    user_id: UUID | None = None,
    memory_id: UUID | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[dict], int]:
    filters = []
    if memory_id:
        filters.append(Share.memory_object_id == memory_id)

    total = int(
        await session.scalar(select(func.count()).select_from(Share).where(*filters)) or 0
    )
    rows = (
        await session.scalars(
            select(Share)
            .where(*filters)
            .order_by(Share.created_at.desc())
            .limit(min(limit, 200))
            .offset(max(offset, 0))
        )
    ).all()
    items = []
    for s in rows:
        from note_db.models import MemoryObject

        mem = await session.get(MemoryObject, s.memory_object_id)
        if user_id and mem and mem.user_id != user_id:
            continue
        owner = await session.get(User, mem.user_id) if mem else None
        target_user = (
            await session.get(User, s.shared_with_id) if s.shared_with_id else None
        )
        items.append(
            {
                "id": str(s.id),
                "memory_object_id": str(s.memory_object_id),
                "owner_id": str(mem.user_id) if mem else None,
                "owner_email": owner.email if owner else None,
                "shared_with_id": str(s.shared_with_id) if s.shared_with_id else None,
                "shared_with_email": target_user.email if target_user else None,
                "shared_with_group_id": (
                    str(s.shared_with_group_id) if s.shared_with_group_id else None
                ),
                "conversation_id": str(s.conversation_id) if s.conversation_id else None,
                "permissions": s.permissions,
                "created_at": s.created_at.isoformat(),
            }
        )
    return items, total


async def revoke_share(session: AsyncSession, share_id: UUID) -> dict:
    share = await session.get(Share, share_id)
    if not share:
        raise ValueError("Share not found")
    await session.delete(share)
    await session.flush()
    return {"ok": True, "share_id": str(share_id)}


async def list_conversations(
    session: AsyncSession,
    *,
    kind: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[dict], int]:
    filters = []
    if kind:
        filters.append(Conversation.kind == kind)

    total = int(
        await session.scalar(select(func.count()).select_from(Conversation).where(*filters)) or 0
    )
    rows = (
        await session.scalars(
            select(Conversation)
            .where(*filters)
            .order_by(Conversation.created_at.desc())
            .limit(min(limit, 200))
            .offset(max(offset, 0))
        )
    ).all()
    items = []
    for c in rows:
        participants = (
            await session.scalars(
                select(ConversationParticipant).where(
                    ConversationParticipant.conversation_id == c.id
                )
            )
        ).all()
        share_count = int(
            await session.scalar(
                select(func.count()).select_from(Share).where(Share.conversation_id == c.id)
            )
            or 0
        )
        items.append(
            {
                "id": str(c.id),
                "kind": c.kind,
                "title": c.title,
                "group_id": str(c.group_id) if c.group_id else None,
                "participant_count": len(participants),
                "share_count": share_count,
                "created_at": c.created_at.isoformat(),
            }
        )
    return items, total


async def block_reset_user(session: AsyncSession, user_id: UUID) -> dict:
    """Remove blocked friendship edges involving user (both directions)."""
    rows = (
        await session.scalars(
            select(Friendship).where(
                Friendship.status == "blocked",
                (Friendship.user_id == user_id) | (Friendship.friend_id == user_id),
            )
        )
    ).all()
    count = 0
    for f in rows:
        await session.delete(f)
        count += 1
    await session.flush()
    return {"ok": True, "removed": count}
