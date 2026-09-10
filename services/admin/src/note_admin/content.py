"""Admin content domain: helpers, facts, people, categories, search."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from note_db.models import Category, Helper, HelperStatus, MemoryFact, MemoryObject, Person, User


async def list_user_helpers(
    session: AsyncSession,
    user_id: UUID,
    *,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[dict], int]:
    filters = [MemoryObject.user_id == user_id]
    if status:
        filters.append(Helper.status == status)

    total = int(
        await session.scalar(
            select(func.count())
            .select_from(Helper)
            .join(MemoryObject, MemoryObject.id == Helper.memory_object_id)
            .where(*filters)
        )
        or 0
    )
    rows = (
        await session.scalars(
            select(Helper)
            .join(MemoryObject, MemoryObject.id == Helper.memory_object_id)
            .where(*filters)
            .order_by(Helper.created_at.desc())
            .limit(min(limit, 200))
            .offset(max(offset, 0))
        )
    ).all()
    return [_helper_dict(h, user_id) for h in rows], total


async def list_helpers(
    session: AsyncSession,
    *,
    q: str | None = None,
    status: str | None = None,
    source: str | None = None,
    kind: str | None = None,
    user_id: UUID | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[dict], int]:
    filters = []
    if user_id:
        filters.append(MemoryObject.user_id == user_id)
    if status:
        filters.append(Helper.status == status)
    if source:
        filters.append(Helper.source == source)
    if kind:
        filters.append(Helper.kind == kind)
    if q:
        like = f"%{q.strip()}%"
        filters.append(or_(Helper.key.ilike(like), Helper.title.ilike(like), Helper.value.ilike(like)))

    total = int(
        await session.scalar(
            select(func.count())
            .select_from(Helper)
            .join(MemoryObject, MemoryObject.id == Helper.memory_object_id)
            .where(*filters)
        )
        or 0
    )
    rows = (
        await session.scalars(
            select(Helper)
            .join(MemoryObject, MemoryObject.id == Helper.memory_object_id)
            .where(*filters)
            .order_by(Helper.created_at.desc())
            .limit(min(limit, 200))
            .offset(max(offset, 0))
        )
    ).all()
    items = []
    for h in rows:
        mem = await session.get(MemoryObject, h.memory_object_id)
        uid = mem.user_id if mem else user_id
        d = _helper_dict(h, uid)
        user = await session.get(User, uid) if uid else None
        d["user_email"] = user.email if user else None
        items.append(d)
    return items, total


async def get_helper(session: AsyncSession, helper_id: UUID) -> dict:
    helper = await session.get(Helper, helper_id)
    if not helper:
        raise ValueError("Helper not found")
    mem = await session.get(MemoryObject, helper.memory_object_id)
    d = _helper_dict(helper, mem.user_id if mem else None)
    user = await session.get(User, mem.user_id) if mem else None
    d["memory_preview"] = (
        (mem.content_text or mem.structured_title or "")[:160] if mem else None
    )
    d["user_email"] = user.email if user else None
    return d


async def update_helper_status(session: AsyncSession, helper_id: UUID, status: str) -> dict:
    helper = await session.get(Helper, helper_id)
    if not helper:
        raise ValueError("Helper not found")
    helper.status = HelperStatus(status)
    await session.flush()
    mem = await session.get(MemoryObject, helper.memory_object_id)
    return _helper_dict(helper, mem.user_id if mem else None)


def _helper_dict(h: Helper, user_id: UUID | None = None) -> dict:
    return {
        "id": str(h.id),
        "user_id": str(user_id) if user_id else None,
        "memory_object_id": str(h.memory_object_id),
        "key": h.key,
        "title": h.title,
        "value": h.value,
        "kind": h.kind,
        "source": h.source.value if hasattr(h.source, "value") else str(h.source),
        "status": h.status.value if hasattr(h.status, "value") else str(h.status),
        "semantic_type": h.semantic_type,
        "person_id": str(h.person_id) if h.person_id else None,
        "confidence": h.confidence,
        "created_at": h.created_at.isoformat(),
    }


async def list_user_facts(
    session: AsyncSession,
    user_id: UUID,
    *,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[dict], int]:
    filters = [MemoryFact.user_id == user_id]
    total = int(
        await session.scalar(select(func.count()).select_from(MemoryFact).where(*filters)) or 0
    )
    rows = (
        await session.scalars(
            select(MemoryFact)
            .where(*filters)
            .order_by(MemoryFact.updated_at.desc())
            .limit(min(limit, 200))
            .offset(max(offset, 0))
        )
    ).all()
    return [_fact_dict(f) for f in rows], total


async def list_facts(
    session: AsyncSession,
    *,
    q: str | None = None,
    user_id: UUID | None = None,
    person_id: UUID | None = None,
    semantic_type: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[dict], int]:
    filters = []
    if user_id:
        filters.append(MemoryFact.user_id == user_id)
    if person_id:
        filters.append(MemoryFact.person_id == person_id)
    if semantic_type:
        filters.append(MemoryFact.semantic_type == semantic_type)
    if q:
        like = f"%{q.strip()}%"
        filters.append(or_(MemoryFact.key.ilike(like), MemoryFact.value.ilike(like)))

    total = int(
        await session.scalar(select(func.count()).select_from(MemoryFact).where(*filters)) or 0
    )
    rows = (
        await session.scalars(
            select(MemoryFact)
            .where(*filters)
            .order_by(MemoryFact.updated_at.desc())
            .limit(min(limit, 200))
            .offset(max(offset, 0))
        )
    ).all()
    items = []
    for f in rows:
        d = _fact_dict(f)
        user = await session.get(User, f.user_id)
        d["user_email"] = user.email if user else None
        items.append(d)
    return items, total


def _fact_dict(f: MemoryFact) -> dict:
    return {
        "id": str(f.id),
        "user_id": str(f.user_id),
        "key": f.key,
        "display_label": f.display_label,
        "value": f.value,
        "person": f.person,
        "person_id": str(f.person_id) if f.person_id else None,
        "semantic_type": f.semantic_type,
        "fact_type": f.fact_type,
        "user_override": f.user_override,
        "created_at": f.created_at.isoformat(),
        "updated_at": f.updated_at.isoformat(),
    }


async def list_user_people(
    session: AsyncSession,
    user_id: UUID,
    *,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[dict], int]:
    filters = [Person.user_id == user_id]
    total = int(
        await session.scalar(select(func.count()).select_from(Person).where(*filters)) or 0
    )
    rows = (
        await session.scalars(
            select(Person)
            .where(*filters)
            .order_by(Person.display_name.asc())
            .limit(min(limit, 200))
            .offset(max(offset, 0))
        )
    ).all()
    return [_person_dict(p) for p in rows], total


async def list_people(
    session: AsyncSession,
    *,
    q: str | None = None,
    user_id: UUID | None = None,
    has_merge: bool | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[dict], int]:
    filters = []
    if user_id:
        filters.append(Person.user_id == user_id)
    if has_merge is True:
        filters.append(Person.merged_into_id.is_not(None))
    elif has_merge is False:
        filters.append(Person.merged_into_id.is_(None))
    if q:
        like = f"%{q.strip()}%"
        filters.append(
            or_(Person.display_name.ilike(like), Person.normalized_name.ilike(like))
        )

    total = int(
        await session.scalar(select(func.count()).select_from(Person).where(*filters)) or 0
    )
    rows = (
        await session.scalars(
            select(Person)
            .where(*filters)
            .order_by(Person.updated_at.desc())
            .limit(min(limit, 200))
            .offset(max(offset, 0))
        )
    ).all()
    items = []
    for p in rows:
        d = _person_dict(p)
        user = await session.get(User, p.user_id)
        d["user_email"] = user.email if user else None
        items.append(d)
    return items, total


def _person_dict(p: Person) -> dict:
    return {
        "id": str(p.id),
        "user_id": str(p.user_id),
        "display_name": p.display_name,
        "relationship": p.relationship,
        "source": p.source,
        "merged_into_id": str(p.merged_into_id) if p.merged_into_id else None,
        "created_at": p.created_at.isoformat(),
        "updated_at": p.updated_at.isoformat(),
    }


async def list_user_categories(
    session: AsyncSession,
    user_id: UUID,
    *,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[dict], int]:
    filters = [Category.user_id == user_id]
    total = int(
        await session.scalar(select(func.count()).select_from(Category).where(*filters)) or 0
    )
    rows = (
        await session.scalars(
            select(Category)
            .where(*filters)
            .order_by(Category.name.asc())
            .limit(min(limit, 200))
            .offset(max(offset, 0))
        )
    ).all()
    items = []
    for c in rows:
        count = int(
            await session.scalar(
                select(func.count())
                .select_from(Category)
                .where(Category.id == c.id)
            )
            or 0
        )
        # memory count via memory_categories would be better - simplified:
        from note_db.models import MemoryCategory

        mem_count = int(
            await session.scalar(
                select(func.count())
                .select_from(MemoryCategory)
                .where(MemoryCategory.category_id == c.id)
            )
            or 0
        )
        items.append(
            {
                "id": str(c.id),
                "user_id": str(c.user_id),
                "name": c.name,
                "color": c.color,
                "source": c.source.value if hasattr(c.source, "value") else str(c.source),
                "memory_count": mem_count,
                "created_at": c.created_at.isoformat(),
            }
        )
    return items, total


async def list_categories(
    session: AsyncSession,
    *,
    user_id: UUID | None = None,
    source: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[dict], int]:
    items, total = await list_user_categories(
        session, user_id, limit=limit, offset=offset
    ) if user_id else await _list_all_categories(session, source, limit, offset)
    return items, total


async def _list_all_categories(
    session: AsyncSession,
    source: str | None,
    limit: int,
    offset: int,
) -> tuple[list[dict], int]:
    from note_db.models import CategorySource, MemoryCategory

    filters = []
    if source:
        filters.append(Category.source == CategorySource(source))
    total = int(
        await session.scalar(select(func.count()).select_from(Category).where(*filters)) or 0
    )
    rows = (
        await session.scalars(
            select(Category)
            .where(*filters)
            .order_by(Category.created_at.desc())
            .limit(min(limit, 200))
            .offset(max(offset, 0))
        )
    ).all()
    items = []
    for c in rows:
        mem_count = int(
            await session.scalar(
                select(func.count())
                .select_from(MemoryCategory)
                .where(MemoryCategory.category_id == c.id)
            )
            or 0
        )
        user = await session.get(User, c.user_id)
        items.append(
            {
                "id": str(c.id),
                "user_id": str(c.user_id),
                "user_email": user.email if user else None,
                "name": c.name,
                "color": c.color,
                "source": c.source.value if hasattr(c.source, "value") else str(c.source),
                "memory_count": mem_count,
                "created_at": c.created_at.isoformat(),
            }
        )
    return items, total


async def global_search(session: AsyncSession, q: str, *, limit: int = 5) -> dict:
    if not q.strip():
        return {"users": [], "memories": [], "facts": []}
    like = f"%{q.strip()}%"
    cap = min(max(limit, 1), 20)

    users = (
        await session.scalars(
            select(User)
            .where(
                or_(
                    User.email.ilike(like),
                    User.username.ilike(like),
                    User.display_name.ilike(like),
                )
            )
            .limit(cap)
        )
    ).all()

    memories = (
        await session.scalars(
            select(MemoryObject)
            .where(
                or_(
                    MemoryObject.content_text.ilike(like),
                    MemoryObject.structured_title.ilike(like),
                )
            )
            .order_by(MemoryObject.updated_at.desc())
            .limit(cap)
        )
    ).all()

    facts = (
        await session.scalars(
            select(MemoryFact)
            .where(or_(MemoryFact.key.ilike(like), MemoryFact.value.ilike(like)))
            .limit(cap)
        )
    ).all()

    return {
        "users": [
            {
                "id": str(u.id),
                "email": u.email,
                "username": u.username,
                "display_name": u.display_name,
            }
            for u in users
        ],
        "memories": [
            {
                "id": str(m.id),
                "user_id": str(m.user_id),
                "preview": (m.content_text or m.structured_title or "")[:120] or None,
            }
            for m in memories
        ],
        "facts": [
            {
                "id": str(f.id),
                "user_id": str(f.user_id),
                "key": f.key,
                "value": f.value[:80] if f.value else None,
            }
            for f in facts
        ],
    }
