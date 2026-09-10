from uuid import UUID, uuid4

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from note_core.hlc import HLCClock
from note_db.models import Category, CategorySource, MemoryCategory, MemoryObject


class CategoryCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    color: str | None = None


class CategoryResponse(BaseModel):
    id: UUID
    name: str
    color: str | None
    source: CategorySource

    model_config = {"from_attributes": True}


class AssignCategoryRequest(BaseModel):
    category_id: UUID


class CategorizationService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_categories(self, user_id: UUID) -> list[CategoryResponse]:
        cats = (
            await self.session.scalars(
                select(Category).where(Category.user_id == user_id).order_by(Category.name)
            )
        ).all()
        return [CategoryResponse.model_validate(c) for c in cats]

    async def create_category(
        self, user_id: UUID, req: CategoryCreateRequest, device_id: str
    ) -> CategoryResponse:
        existing = await self.session.scalar(
            select(Category).where(Category.user_id == user_id, Category.name == req.name)
        )
        if existing:
            raise ValueError("Category already exists")

        hlc = HLCClock(device_id).now().to_string()
        cat = Category(
            id=uuid4(),
            user_id=user_id,
            name=req.name,
            color=req.color,
            source=CategorySource.user,
            hlc=hlc,
        )
        self.session.add(cat)
        await self.session.flush()
        return CategoryResponse.model_validate(cat)

    async def assign_to_memory(
        self, user_id: UUID, memory_id: UUID, category_id: UUID, device_id: str
    ) -> None:
        mem = await self.session.get(MemoryObject, memory_id)
        if not mem or mem.user_id != user_id:
            raise ValueError("Memory not found")
        cat = await self.session.get(Category, category_id)
        if not cat or cat.user_id != user_id:
            raise ValueError("Category not found")

        ai_links = (
            await self.session.scalars(
                select(MemoryCategory).where(
                    MemoryCategory.memory_object_id == memory_id,
                    MemoryCategory.source == CategorySource.ai,
                )
            )
        ).all()
        for link in ai_links:
            await self.session.delete(link)

        existing = await self.session.scalar(
            select(MemoryCategory).where(
                MemoryCategory.memory_object_id == memory_id,
                MemoryCategory.category_id == category_id,
            )
        )
        if not existing:
            hlc = HLCClock(device_id).now().to_string()
            self.session.add(
                MemoryCategory(
                    memory_object_id=memory_id,
                    category_id=category_id,
                    source=CategorySource.user,
                    hlc=hlc,
                )
            )
