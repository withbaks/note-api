"""Cascade deletion for memory objects and derived artifacts."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from note_db.models import (
    Entity,
    Helper,
    Lifecycle,
    MemoryCategory,
    MemoryFact,
    MemoryObject,
    MemoryPerson,
    SearchDocument,
    Understanding,
)
from note_sync.server_sync import ServerSyncEmitter


class MemoryDeletionService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self._emitter = ServerSyncEmitter(session)

    async def delete_memory(self, user_id: UUID, memory_id: UUID) -> None:
        obj = await self.session.get(MemoryObject, memory_id)
        if not obj or obj.user_id != user_id:
            raise ValueError("Memory not found")

        obj.lifecycle = Lifecycle.deleted
        obj.deleted_at = datetime.now(UTC)

        helpers = (
            await self.session.scalars(select(Helper).where(Helper.memory_object_id == memory_id))
        ).all()
        for helper in helpers:
            await self._emitter._emit(
                user_id, "helper", helper.id, "delete", {"id": str(helper.id)}
            )
            await self.session.delete(helper)

        memory_categories = (
            await self.session.scalars(
                select(MemoryCategory).where(MemoryCategory.memory_object_id == memory_id)
            )
        ).all()
        for link in memory_categories:
            await self._emitter._emit(
                user_id,
                "memory_category",
                link.id,
                "delete",
                {"id": str(link.id)},
            )
            await self.session.delete(link)

        await self.session.execute(
            delete(MemoryPerson).where(MemoryPerson.memory_object_id == memory_id)
        )
        await self.session.execute(delete(Entity).where(Entity.memory_object_id == memory_id))
        await self.session.execute(
            delete(SearchDocument).where(SearchDocument.memory_object_id == memory_id)
        )

        understandings = (
            await self.session.scalars(
                select(Understanding).where(Understanding.memory_object_id == memory_id)
            )
        ).all()
        for understanding in understandings:
            await self._emitter._emit(
                user_id,
                "understanding",
                understanding.id,
                "delete",
                {"id": str(understanding.id)},
            )
            await self.session.delete(understanding)

        mem_id_str = str(memory_id)
        facts = (
            await self.session.scalars(select(MemoryFact).where(MemoryFact.user_id == user_id))
        ).all()
        for fact in facts:
            source_ids = list(fact.source_memory_ids or [])
            if mem_id_str not in source_ids:
                continue
            source_ids = [s for s in source_ids if s != mem_id_str]
            if not source_ids:
                await self._emitter._emit(
                    user_id,
                    "memory_fact",
                    fact.id,
                    "delete",
                    {"id": str(fact.id)},
                )
                await self.session.delete(fact)
            else:
                fact.source_memory_ids = source_ids
                await self._emitter.emit_fact(user_id, fact, operation="update")

        from note_sync.service import SyncService

        await self._emitter._emit(
            user_id,
            "memory_object",
            obj.id,
            "update",
            SyncService._serialize_memory(obj),
        )
        await self.session.flush()
