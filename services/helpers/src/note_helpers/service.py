from datetime import datetime
from uuid import UUID, uuid4

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from note_core.hlc import HLCClock
from note_db.models import Helper, HelperStatus, MemoryObject, MemoryThread, Thread
from note_helpers.facts import FACT_FROM_HELPER, upsert_fact_from_helper


class HelperActionRequest(BaseModel):
    status: HelperStatus


class UpcomingItem(BaseModel):
    helper_id: UUID
    memory_object_id: UUID
    key: str
    value: str | None
    person: str | None
    person_id: UUID | None = None
    semantic_type: str | None = None
    scheduled_at: datetime | None
    content_preview: str | None


class HelpersService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def update_helper_status(
        self, user_id: UUID, helper_id: UUID, status: HelperStatus
    ) -> None:
        helper = await self.session.get(Helper, helper_id)
        if not helper:
            raise ValueError("Helper not found")
        mem = await self.session.get(MemoryObject, helper.memory_object_id)
        if not mem or mem.user_id != user_id:
            raise ValueError("Not authorized")
        helper.status = status

        if status == HelperStatus.accepted:
            await self.apply_accepted_helper_side_effects(user_id, helper, mem)

    async def apply_accepted_helper_side_effects(
        self, user_id: UUID, helper: Helper, mem: MemoryObject
    ) -> MemoryThread | None:
        """Run on accept: fact upsert, thread attach, etc. Returns new memory_thread if any."""
        meta = helper.helper_metadata if isinstance(helper.helper_metadata, dict) else {}

        if helper.tool_name == "confirm_thread" or helper.key == "confirm_thread":
            thread_id_raw = meta.get("thread_id")
            if thread_id_raw:
                return await self._attach_to_thread(user_id, mem, UUID(str(thread_id_raw)))
            return None

        if (
            helper.tool_name == "review_fact"
            or (helper.key and str(helper.key).startswith("review_fact:"))
            or (helper.key and str(helper.key).startswith("still_true:"))
        ):
            if helper.proposed_fact_key or helper.key in FACT_FROM_HELPER:
                await upsert_fact_from_helper(
                    self.session,
                    user_id,
                    helper,
                    mem.id,
                    allow_conflict_overwrite=True,
                )
            return None

        if helper.kind in ("system", "action") and not helper.proposed_fact_key:
            return None

        if helper.key in FACT_FROM_HELPER or helper.proposed_fact_key:
            await upsert_fact_from_helper(
                self.session,
                user_id,
                helper,
                mem.id,
                allow_conflict_overwrite=True,
            )
        return None

    async def _attach_to_thread(
        self, user_id: UUID, mem: MemoryObject, thread_id: UUID
    ) -> MemoryThread | None:
        thread = await self.session.get(Thread, thread_id)
        if not thread or thread.user_id != user_id:
            return None
        existing = await self.session.scalar(
            select(MemoryThread).where(
                MemoryThread.memory_object_id == mem.id,
                MemoryThread.thread_id == thread_id,
            )
        )
        if existing:
            return existing
        hlc = HLCClock("server").now().to_string()
        link = MemoryThread(
            id=uuid4(),
            memory_object_id=mem.id,
            thread_id=thread_id,
            hlc=hlc,
        )
        self.session.add(link)
        thread.last_memory_at = mem.created_at
        thread.hlc = hlc
        await self.session.flush()
        return link

    async def list_upcoming(self, user_id: UUID) -> list[UpcomingItem]:
        stmt = (
            select(Helper, MemoryObject)
            .join(MemoryObject, Helper.memory_object_id == MemoryObject.id)
            .where(
                MemoryObject.user_id == user_id,
                Helper.status == HelperStatus.accepted,
                Helper.key.in_(["is_reminder", "is_calendar", "is_birthday"]),
            )
        )
        rows = (await self.session.execute(stmt)).all()
        items = []
        for helper, mem in rows:
            preview = mem.content_text or f"{mem.structured_title}: {mem.structured_value}"
            items.append(
                UpcomingItem(
                    helper_id=helper.id,
                    memory_object_id=mem.id,
                    key=helper.key,
                    value=helper.value,
                    person=helper.person,
                    person_id=helper.person_id,
                    semantic_type=helper.semantic_type,
                    scheduled_at=helper.scheduled_at,
                    content_preview=preview[:100] if preview else None,
                )
            )
        return items
