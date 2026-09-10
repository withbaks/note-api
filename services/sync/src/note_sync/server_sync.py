"""Emit server-originated sync mutations after AI and other server-side writes."""

from __future__ import annotations

from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from note_core.hlc import HLCClock
from note_core.sync_constants import SYSTEM_DEVICE_ID
from note_db.models import (
    Category,
    Helper,
    MemoryCategory,
    MemoryFact,
    MemoryObject,
    MemoryThread,
    SyncMutation,
    Thread,
    Understanding,
)
from note_sync.service import SyncService


class ServerSyncEmitter:
    """Record sync mutations so all user devices receive server-side AI artifacts."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self._clock = HLCClock("server")
        self._sync = SyncService(session)

    def _hlc(self) -> str:
        return self._clock.now().to_string()

    async def _emit(
        self,
        user_id: UUID,
        entity_type: str,
        entity_id: UUID,
        operation: str,
        payload: dict,
    ) -> None:
        mutation_id = uuid4()
        hlc = self._hlc()
        existing = await self.session.get(SyncMutation, mutation_id)
        if existing:
            return
        self.session.add(
            SyncMutation(
                id=mutation_id,
                user_id=user_id,
                device_id=SYSTEM_DEVICE_ID,
                entity_type=entity_type,
                entity_id=entity_id,
                operation=operation,
                payload=payload,
                hlc=hlc,
            )
        )

    async def emit_memory_ai_artifacts(self, user_id: UUID, memory_id: UUID) -> None:
        obj = await self.session.get(MemoryObject, memory_id)
        if not obj or obj.user_id != user_id:
            return

        await self._emit(
            user_id,
            "memory_object",
            obj.id,
            "update",
            SyncService._serialize_memory(obj),
        )

        understanding = await self.session.scalar(
            select(Understanding).where(
                Understanding.memory_object_id == memory_id,
                Understanding.superseded_by.is_(None),
            )
        )
        if understanding:
            await self._emit(
                user_id,
                "understanding",
                understanding.id,
                "create",
                SyncService._serialize_understanding(understanding),
            )

        helpers = (
            await self.session.scalars(
                select(Helper).where(Helper.memory_object_id == memory_id)
            )
        ).all()
        for helper in helpers:
            op = "update" if helper.updated_at != helper.created_at else "create"
            await self._emit(
                user_id,
                "helper",
                helper.id,
                op,
                SyncService._serialize_helper(helper),
            )

        memory_categories = (
            await self.session.scalars(
                select(MemoryCategory).where(MemoryCategory.memory_object_id == memory_id)
            )
        ).all()
        category_ids = {mc.category_id for mc in memory_categories}
        for mc in memory_categories:
            await self._emit(
                user_id,
                "memory_category",
                mc.id,
                "create",
                SyncService._serialize_memory_category(mc),
            )

        if category_ids:
            categories = (
                await self.session.scalars(
                    select(Category).where(Category.id.in_(category_ids))
                )
            ).all()
            for cat in categories:
                await self._emit(
                    user_id,
                    "category",
                    cat.id,
                    "create",
                    SyncService._serialize_category(cat),
                )

        facts = (
            await self.session.scalars(select(MemoryFact).where(MemoryFact.user_id == user_id))
        ).all()
        mem_id_str = str(memory_id)
        for fact in facts:
            if mem_id_str not in (fact.source_memory_ids or []):
                continue
            await self._emit(
                user_id,
                "memory_fact",
                fact.id,
                "create",
                SyncService._serialize_fact(fact),
            )

        memory_threads = (
            await self.session.scalars(
                select(MemoryThread).where(MemoryThread.memory_object_id == memory_id)
            )
        ).all()
        thread_ids = {mt.thread_id for mt in memory_threads}
        for mt in memory_threads:
            await self._emit(
                user_id,
                "memory_thread",
                mt.id,
                "create",
                SyncService._serialize_memory_thread(mt),
            )
        if thread_ids:
            threads = (
                await self.session.scalars(select(Thread).where(Thread.id.in_(thread_ids)))
            ).all()
            for thread in threads:
                await self._emit(
                    user_id,
                    "thread",
                    thread.id,
                    "create",
                    SyncService._serialize_thread(thread),
                )

    async def emit_fact(self, user_id: UUID, fact: MemoryFact, *, operation: str = "create") -> None:
        await self._emit(
            user_id,
            "memory_fact",
            fact.id,
            operation,
            SyncService._serialize_fact(fact),
        )

    async def emit_helper(self, user_id: UUID, helper: Helper, *, operation: str = "update") -> None:
        await self._emit(
            user_id,
            "helper",
            helper.id,
            operation,
            SyncService._serialize_helper(helper),
        )

    async def emit_thread(self, user_id: UUID, thread: Thread, *, operation: str = "create") -> None:
        await self._emit(
            user_id,
            "thread",
            thread.id,
            operation,
            SyncService._serialize_thread(thread),
        )

    async def emit_memory_thread(
        self, user_id: UUID, link: MemoryThread, *, operation: str = "create"
    ) -> None:
        await self._emit(
            user_id,
            "memory_thread",
            link.id,
            operation,
            SyncService._serialize_memory_thread(link),
        )
