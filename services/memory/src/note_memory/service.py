from datetime import datetime
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from note_core.hlc import HLC
from note_db.models import (
    AIState,
    Helper,
    HelperSource,
    HelperStatus,
    Lifecycle,
    MemoryFact,
    MemoryObject,
    MemoryType,
    MemoryVersion,
    Understanding,
    Visibility,
)


class MemoryObjectResponse(BaseModel):
    id: UUID
    user_id: UUID
    type: MemoryType
    origin: str
    content_text: str | None
    structured_title: str | None
    structured_value: str | None
    visibility: Visibility
    lifecycle: Lifecycle
    favorite: bool
    ai_state: AIState
    device_id: UUID | None
    hlc: str
    version: int
    deleted_at: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class UnderstandingResponse(BaseModel):
    id: UUID
    memory_object_id: UUID
    summary: str | None
    tags: list | None
    topics: list | None
    connections: list | None
    why_it_matters: str | None
    keyword_summary: str | None
    superseded_by: UUID | None
    created_at: datetime

    model_config = {"from_attributes": True}


class HelperResponse(BaseModel):
    id: UUID
    memory_object_id: UUID
    key: str
    value: str | None
    person: str | None
    person_id: UUID | None = None
    semantic_type: str | None = None
    source: HelperSource
    status: HelperStatus
    scheduled_at: datetime | None
    hlc: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class MemoryVersionResponse(BaseModel):
    id: UUID
    memory_object_id: UUID
    version: int
    snapshot: dict
    hlc: str
    device_id: UUID | None
    created_at: datetime

    model_config = {"from_attributes": True}


class MemoryFactResponse(BaseModel):
    id: UUID
    user_id: UUID
    key: str
    value: str
    person: str | None
    person_id: UUID | None = None
    semantic_type: str | None = None
    user_override: bool
    source_memory_ids: list | None
    hlc: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class MemoryService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_memory(self, user_id: UUID, memory_id: UUID) -> MemoryObjectResponse:
        obj = await self._get_owned(user_id, memory_id)
        return MemoryObjectResponse.model_validate(obj)

    async def list_memories(
        self, user_id: UUID, lifecycle: Lifecycle | None = None, limit: int = 100, offset: int = 0
    ) -> list[MemoryObjectResponse]:
        stmt = select(MemoryObject).where(MemoryObject.user_id == user_id)
        if lifecycle:
            stmt = stmt.where(MemoryObject.lifecycle == lifecycle)
        else:
            stmt = stmt.where(MemoryObject.lifecycle != Lifecycle.deleted)
        stmt = stmt.order_by(MemoryObject.created_at.desc()).limit(limit).offset(offset)
        objs = (await self.session.scalars(stmt)).all()
        return [MemoryObjectResponse.model_validate(o) for o in objs]

    async def get_versions(self, user_id: UUID, memory_id: UUID) -> list[MemoryVersionResponse]:
        await self._get_owned(user_id, memory_id)
        stmt = (
            select(MemoryVersion)
            .where(MemoryVersion.memory_object_id == memory_id)
            .order_by(MemoryVersion.version.desc())
        )
        versions = (await self.session.scalars(stmt)).all()
        return [MemoryVersionResponse.model_validate(v) for v in versions]

    async def get_understandings(self, user_id: UUID, memory_id: UUID) -> list[UnderstandingResponse]:
        await self._get_owned(user_id, memory_id)
        stmt = (
            select(Understanding)
            .where(Understanding.memory_object_id == memory_id, Understanding.superseded_by.is_(None))
            .order_by(Understanding.created_at.desc())
        )
        items = (await self.session.scalars(stmt)).all()
        return [UnderstandingResponse.model_validate(u) for u in items]

    async def get_helpers(self, user_id: UUID, memory_id: UUID) -> list[HelperResponse]:
        await self._get_owned(user_id, memory_id)
        stmt = select(Helper).where(Helper.memory_object_id == memory_id)
        helpers = (await self.session.scalars(stmt)).all()
        return [HelperResponse.model_validate(h) for h in helpers]

    async def list_facts(self, user_id: UUID) -> list[MemoryFactResponse]:
        stmt = select(MemoryFact).where(MemoryFact.user_id == user_id).order_by(MemoryFact.key)
        facts = (await self.session.scalars(stmt)).all()
        return [MemoryFactResponse.model_validate(f) for f in facts]

    async def export_user_data(self, user_id: UUID) -> dict:
        memories = await self.list_memories(user_id, limit=10000)
        facts = await self.list_facts(user_id)
        from note_db.models import Category, MemoryCategory

        categories = (
            await self.session.scalars(select(Category).where(Category.user_id == user_id))
        ).all()
        links = (
            await self.session.scalars(
                select(MemoryCategory).join(MemoryObject).where(MemoryObject.user_id == user_id)
            )
        ).all()

        export_helpers = []
        for m in memories:
            helpers = await self.get_helpers(user_id, m.id)
            export_helpers.extend([h.model_dump(mode="json") for h in helpers])

        understandings = []
        for m in memories:
            u = await self.get_understandings(user_id, m.id)
            understandings.extend([x.model_dump(mode="json") for x in u])

        return {
            "exported_at": datetime.utcnow().isoformat(),
            "memories": [m.model_dump(mode="json") for m in memories],
            "facts": [f.model_dump(mode="json") for f in facts],
            "categories": [
                {"id": str(c.id), "name": c.name, "color": c.color, "source": c.source.value}
                for c in categories
            ],
            "memory_categories": [
                {
                    "memory_object_id": str(l.memory_object_id),
                    "category_id": str(l.category_id),
                    "source": l.source.value,
                }
                for l in links
            ],
            "helpers": export_helpers,
            "understandings": understandings,
        }

    async def _get_owned(self, user_id: UUID, memory_id: UUID) -> MemoryObject:
        obj = await self.session.get(MemoryObject, memory_id)
        if not obj or obj.user_id != user_id:
            raise ValueError("Memory not found")
        return obj

    @staticmethod
    def compare_hlc(a: str, b: str) -> int:
        ha, hb = HLC.from_string(a), HLC.from_string(b)
        if ha > hb:
            return 1
        if ha < hb:
            return -1
        return 0
