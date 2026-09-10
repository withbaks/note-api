from datetime import UTC, datetime
from uuid import UUID

from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from note_core.hlc import HLC
from note_core.sync_constants import SYSTEM_DEVICE_ID
from note_db.models import (
    AIJob,
    AIState,
    Category,
    CategorySource,
    Helper,
    HelperSource,
    HelperStatus,
    Lifecycle,
    MemoryCategory,
    MemoryFact,
    MemoryObject,
    MemoryThread,
    MemoryType,
    MemoryVersion,
    Person,
    SyncCursor,
    SyncMutation,
    Thread,
    Understanding,
    Visibility,
)


class SyncMutationInput(BaseModel):
    id: UUID
    entity_type: str
    entity_id: UUID
    operation: str
    payload: dict
    hlc: str


class PushRequest(BaseModel):
    device_id: UUID
    mutations: list[SyncMutationInput]


class PushResponse(BaseModel):
    applied: list[UUID]
    rejected: list[dict]
    newly_applied: list[UUID] = Field(default_factory=list)


class PullResponse(BaseModel):
    cursor: str
    changes: list[dict]


class HydrateResponse(BaseModel):
    memories: list[dict]
    versions: list[dict]
    understandings: list[dict]
    helpers: list[dict]
    facts: list[dict]
    categories: list[dict]
    memory_categories: list[dict]
    people: list[dict] = Field(default_factory=list)
    cursor: str


SYNCABLE_FIELDS = {
    "memory_object": [
        "type", "content_text", "structured_title", "structured_value",
        "media_uri", "media_type", "link_metadata", "origin",
        "lifecycle", "favorite", "visibility", "version", "deleted_at",
    ],
    "helper": [
        "key", "title", "description", "kind", "proposed_fact_key", "span_start", "span_end",
        "confidence", "metadata", "tool_name", "value", "person", "person_id", "semantic_type",
        "source", "status", "scheduled_at",
    ],
    "memory_fact": ["key", "display_label", "fact_type", "value", "person", "person_id", "semantic_type", "user_override"],
    "category": ["name", "color"],
    "person": ["display_name", "normalized_name", "relationship", "aliases", "merged_into_id", "source", "contact_link_id"],
}


MUTATION_PRIORITY = {
    "memory_object": 0,
    "person": 10,
    "category": 20,
    "memory_category": 30,
    "helper": 40,
    "memory_fact": 50,
}


class SyncService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self._category_id_aliases: dict[UUID, UUID] = {}

    async def push(self, user_id: UUID, req: PushRequest) -> PushResponse:
        self._category_id_aliases = {}
        applied: list[UUID] = []
        newly_applied: list[UUID] = []
        rejected: list[dict] = []

        ordered = sorted(
            req.mutations,
            key=lambda m: (MUTATION_PRIORITY.get(m.entity_type, 100), m.hlc),
        )

        for mutation in ordered:
            existing = await self.session.get(SyncMutation, mutation.id)
            if existing:
                applied.append(mutation.id)
                continue

            try:
                async with self.session.begin_nested():
                    await self._apply_mutation(user_id, req.device_id, mutation)
                    self.session.add(
                        SyncMutation(
                            id=mutation.id,
                            user_id=user_id,
                            device_id=req.device_id,
                            entity_type=mutation.entity_type,
                            entity_id=mutation.entity_id,
                            operation=mutation.operation,
                            payload=mutation.payload,
                            hlc=mutation.hlc,
                        )
                    )
                applied.append(mutation.id)
                newly_applied.append(mutation.id)
            except Exception as exc:
                rejected.append({"id": str(mutation.id), "error": str(exc)})

        # Client cursor is authoritative for pull; only update server cursor on pull/hydrate
        return PushResponse(applied=applied, newly_applied=newly_applied, rejected=rejected)

    async def pull(self, user_id: UUID, device_id: UUID, cursor: str = "0") -> PullResponse:
        stmt = (
            select(SyncMutation)
            .where(
                SyncMutation.user_id == user_id,
                or_(
                    SyncMutation.device_id != device_id,
                    SyncMutation.device_id == SYSTEM_DEVICE_ID,
                ),
            )
            .order_by(SyncMutation.applied_at.asc())
        )
        if cursor != "0":
            try:
                cursor_time = datetime.fromisoformat(cursor)
                stmt = stmt.where(SyncMutation.applied_at > cursor_time)
            except ValueError:
                pass

        mutations = (await self.session.scalars(stmt.limit(500))).all()
        changes = [
            {
                "entity_type": m.entity_type,
                "entity_id": str(m.entity_id),
                "operation": m.operation,
                "payload": m.payload,
                "hlc": m.hlc,
                "applied_at": m.applied_at.isoformat(),
            }
            for m in mutations
        ]
        new_cursor = mutations[-1].applied_at.isoformat() if mutations else cursor
        await self._set_cursor(user_id, device_id, new_cursor)
        return PullResponse(cursor=new_cursor, changes=changes)

    async def hydrate(self, user_id: UUID, device_id: UUID) -> HydrateResponse:
        memories = (
            await self.session.scalars(
                select(MemoryObject).where(
                    MemoryObject.user_id == user_id,
                    MemoryObject.lifecycle != Lifecycle.deleted,
                )
            )
        ).all()

        memory_ids = [m.id for m in memories]
        versions, understandings, helpers, facts, categories, memory_categories = [], [], [], [], [], []

        if memory_ids:
            versions = (
                await self.session.scalars(
                    select(MemoryVersion).where(MemoryVersion.memory_object_id.in_(memory_ids))
                )
            ).all()
            understandings = (
                await self.session.scalars(
                    select(Understanding).where(
                        Understanding.memory_object_id.in_(memory_ids),
                        Understanding.superseded_by.is_(None),
                    )
                )
            ).all()
            helpers = (
                await self.session.scalars(
                    select(Helper).where(Helper.memory_object_id.in_(memory_ids))
                )
            ).all()

        facts = (
            await self.session.scalars(select(MemoryFact).where(MemoryFact.user_id == user_id))
        ).all()
        categories = (
            await self.session.scalars(select(Category).where(Category.user_id == user_id))
        ).all()
        people = (
            await self.session.scalars(
                select(Person).where(Person.user_id == user_id, Person.merged_into_id.is_(None))
            )
        ).all()
        # Ensure self person exists for older accounts
        if not any(p.relationship == "self" for p in people):
            from note_db.models import User
            from note_db.people import PeopleService

            user = await self.session.get(User, user_id)
            if user:
                self_person = await PeopleService(self.session).ensure_self(user)
                people = list(people) + [self_person]

        if memory_ids:
            memory_categories = (
                await self.session.scalars(
                    select(MemoryCategory).where(MemoryCategory.memory_object_id.in_(memory_ids))
                )
            ).all()

        cursor = datetime.now(UTC).isoformat()
        await self._set_cursor(user_id, device_id, cursor)

        return HydrateResponse(
            memories=[self._serialize_memory(m) for m in memories],
            versions=[self._serialize_version(v) for v in versions],
            understandings=[self._serialize_understanding(u) for u in understandings],
            helpers=[self._serialize_helper(h) for h in helpers],
            facts=[self._serialize_fact(f) for f in facts],
            categories=[self._serialize_category(c) for c in categories],
            memory_categories=[self._serialize_memory_category(mc) for mc in memory_categories],
            people=[self._serialize_person(p) for p in people],
            cursor=cursor,
        )

    async def _apply_mutation(self, user_id: UUID, device_id: UUID, mutation: SyncMutationInput) -> None:
        from note_sync.registry import get_sync_applier

        applier = get_sync_applier(self, mutation.entity_type)
        if not applier:
            raise ValueError(f"Unknown entity type: {mutation.entity_type}")
        if mutation.entity_type == "memory_object":
            await applier(user_id, device_id, mutation)
        else:
            await applier(user_id, mutation)

    async def _apply_memory_mutation(
        self, user_id: UUID, device_id: UUID, mutation: SyncMutationInput
    ) -> None:
        payload = mutation.payload
        obj = await self.session.get(MemoryObject, mutation.entity_id)

        if mutation.operation == "create":
            if obj:
                if self._hlc_wins(mutation.hlc, obj.hlc):
                    self._merge_fields(obj, payload, mutation.hlc)
                return
            mem_type = payload.get("type", "text")
            try:
                resolved_type = MemoryType(mem_type)
            except ValueError:
                resolved_type = MemoryType.text
            obj = MemoryObject(
                id=mutation.entity_id,
                user_id=user_id,
                type=resolved_type,
                origin=payload.get("origin", "capture"),
                content_text=payload.get("content_text"),
                structured_title=payload.get("structured_title"),
                structured_value=payload.get("structured_value"),
                media_uri=payload.get("media_uri"),
                media_type=payload.get("media_type"),
                link_metadata=payload.get("link_metadata"),
                visibility=Visibility(payload.get("visibility", "private")),
                lifecycle=Lifecycle(payload.get("lifecycle", "active")),
                favorite=payload.get("favorite", False),
                ai_state=AIState.captured,
                device_id=device_id,
                hlc=mutation.hlc,
                version=payload.get("version", 1),
            )
            self.session.add(obj)
            await self.session.flush()

            if obj.type == MemoryType.structured and obj.structured_title and obj.structured_value:
                await self._create_structured_helper_and_fact(user_id, obj, mutation.hlc)

            job = AIJob(memory_object_id=obj.id, state=AIState.captured)
            self.session.add(job)
            return

        if not obj or obj.user_id != user_id:
            raise ValueError("Memory not found")

        if mutation.operation == "update":
            if not self._hlc_wins(mutation.hlc, obj.hlc):
                return

            snapshot = self._serialize_memory(obj)
            version = MemoryVersion(
                memory_object_id=obj.id,
                version=obj.version,
                snapshot=snapshot,
                hlc=obj.hlc,
                device_id=obj.device_id,
            )
            self.session.add(version)

            self._merge_fields(obj, payload, mutation.hlc)
            obj.version += 1
            obj.device_id = device_id

            if payload.get("lifecycle") == Lifecycle.deleted.value:
                obj.deleted_at = datetime.now(UTC)
                from note_sync.deletion import MemoryDeletionService

                await MemoryDeletionService(self.session).delete_memory(user_id, obj.id)
            elif payload.get("lifecycle") == Lifecycle.active.value:
                obj.deleted_at = None

        elif mutation.operation == "delete":
            from note_sync.deletion import MemoryDeletionService

            await MemoryDeletionService(self.session).delete_memory(user_id, obj.id)
            obj.hlc = mutation.hlc

    async def _apply_helper_mutation(self, user_id: UUID, mutation: SyncMutationInput) -> None:
        payload = mutation.payload
        helper = await self.session.get(Helper, mutation.entity_id)

        if mutation.operation == "create":
            memory_id = UUID(payload["memory_object_id"])
            mem = await self.session.get(MemoryObject, memory_id)
            if not mem or mem.user_id != user_id:
                raise ValueError("Memory not found")
            if helper:
                if self._hlc_wins(mutation.hlc, helper.hlc):
                    self._merge_helper_fields(helper, payload, mutation.hlc)
                return
            person_id = await self._ensure_person_exists(
                user_id,
                UUID(payload["person_id"]) if payload.get("person_id") else None,
                person_label=payload.get("person"),
                hlc=mutation.hlc,
            )
            helper = Helper(
                id=mutation.entity_id,
                memory_object_id=memory_id,
                key=payload["key"],
                title=payload.get("title") or payload["key"],
                description=payload.get("description"),
                kind=payload.get("kind", "knowledge"),
                proposed_fact_key=payload.get("proposed_fact_key"),
                span_start=payload.get("span_start"),
                span_end=payload.get("span_end"),
                confidence=payload.get("confidence"),
                helper_metadata=payload.get("metadata"),
                tool_name=payload.get("tool_name"),
                value=payload.get("value"),
                person=payload.get("person"),
                person_id=person_id,
                semantic_type=payload.get("semantic_type"),
                source=HelperSource(payload.get("source", "user")),
                status=HelperStatus(payload.get("status", "accepted")),
                scheduled_at=payload.get("scheduled_at"),
                hlc=mutation.hlc,
            )
            self.session.add(helper)
            return

        if not helper:
            raise ValueError("Helper not found")
        mem = await self.session.get(MemoryObject, helper.memory_object_id)
        if not mem or mem.user_id != user_id:
            raise ValueError("Not authorized")

        if self._hlc_wins(mutation.hlc, helper.hlc):
            prev_status = helper.status
            if payload.get("person_id"):
                await self._ensure_person_exists(
                    user_id,
                    UUID(payload["person_id"]),
                    person_label=payload.get("person"),
                    hlc=mutation.hlc,
                )
            self._merge_helper_fields(helper, payload, mutation.hlc)
            if (
                helper.status == HelperStatus.accepted
                and prev_status != HelperStatus.accepted
            ):
                from note_helpers.service import HelpersService
                from note_sync.server_sync import ServerSyncEmitter

                link = await HelpersService(self.session).apply_accepted_helper_side_effects(
                    user_id, helper, mem
                )
                emitter = ServerSyncEmitter(self.session)
                if link:
                    thread = await self.session.get(Thread, link.thread_id)
                    if thread:
                        await emitter.emit_thread(user_id, thread, operation="update")
                    await emitter.emit_memory_thread(user_id, link, operation="create")
                fact = None
                if helper.proposed_fact_key:
                    fact = await self.session.scalar(
                        select(MemoryFact).where(
                            MemoryFact.user_id == user_id,
                            MemoryFact.key == helper.proposed_fact_key,
                            MemoryFact.person_id == helper.person_id,
                        )
                    )
                if fact:
                    await emitter.emit_fact(user_id, fact, operation="update")

    async def _apply_fact_mutation(self, user_id: UUID, mutation: SyncMutationInput) -> None:
        payload = mutation.payload
        fact = await self.session.get(MemoryFact, mutation.entity_id)

        if mutation.operation in ("create", "update"):
            person_id = await self._ensure_person_exists(
                user_id,
                UUID(payload["person_id"]) if payload.get("person_id") else None,
                person_label=payload.get("person"),
                hlc=mutation.hlc,
            )
            if fact:
                if self._hlc_wins(mutation.hlc, fact.hlc):
                    fact.key = payload.get("key", fact.key)
                    fact.display_label = payload.get("display_label", fact.display_label)
                    fact.fact_type = payload.get("fact_type", fact.fact_type)
                    fact.value = payload.get("value", fact.value)
                    fact.person = payload.get("person", fact.person)
                    if payload.get("person_id"):
                        fact.person_id = person_id
                    fact.semantic_type = payload.get("semantic_type", fact.semantic_type)
                    fact.user_override = payload.get("user_override", fact.user_override)
                    if payload.get("mention_count") is not None:
                        fact.mention_count = int(payload["mention_count"])
                    if payload.get("expected_volatility"):
                        fact.expected_volatility = payload["expected_volatility"]
                    if "last_reaffirmed_at" in payload:
                        raw = payload.get("last_reaffirmed_at")
                        fact.last_reaffirmed_at = (
                            datetime.fromisoformat(raw.replace("Z", "+00:00")) if raw else None
                        )
                    if "expires_at" in payload:
                        raw = payload.get("expires_at")
                        fact.expires_at = (
                            datetime.fromisoformat(raw.replace("Z", "+00:00")) if raw else None
                        )
                    fact.hlc = mutation.hlc
                return
            fact = MemoryFact(
                id=mutation.entity_id,
                user_id=user_id,
                key=payload["key"],
                display_label=payload.get("display_label"),
                fact_type=payload.get("fact_type", "attribute"),
                value=payload["value"],
                person=payload.get("person"),
                person_id=person_id,
                semantic_type=payload.get("semantic_type"),
                user_override=payload.get("user_override", True),
                source_memory_ids=payload.get("source_memory_ids"),
                mention_count=int(payload.get("mention_count") or 1),
                expected_volatility=payload.get("expected_volatility") or "slow",
                hlc=mutation.hlc,
            )
            self.session.add(fact)

    async def _apply_category_mutation(self, user_id: UUID, mutation: SyncMutationInput) -> None:
        payload = mutation.payload
        cat = await self.session.get(Category, mutation.entity_id)

        if mutation.operation == "create":
            if cat:
                return
            name = payload["name"]
            existing = await self.session.scalar(
                select(Category).where(Category.user_id == user_id, Category.name == name)
            )
            if existing:
                self._category_id_aliases[mutation.entity_id] = existing.id
                if self._hlc_wins(mutation.hlc, existing.hlc):
                    if payload.get("color") is not None:
                        existing.color = payload.get("color")
                    existing.hlc = mutation.hlc
                return
            cat = Category(
                id=mutation.entity_id,
                user_id=user_id,
                name=name,
                color=payload.get("color"),
                source=CategorySource(payload.get("source", "user")),
                hlc=mutation.hlc,
            )
            self.session.add(cat)

    async def _apply_memory_category_mutation(self, user_id: UUID, mutation: SyncMutationInput) -> None:
        payload = mutation.payload
        link = await self.session.get(MemoryCategory, mutation.entity_id)
        memory_id = UUID(payload["memory_object_id"])
        mem = await self.session.get(MemoryObject, memory_id)
        if not mem or mem.user_id != user_id:
            raise ValueError("Not authorized")

        if mutation.operation == "create" and not link:
            category_id = UUID(payload["category_id"])
            category_id = self._category_id_aliases.get(category_id, category_id)
            cat = await self.session.get(Category, category_id)
            if not cat or cat.user_id != user_id:
                raise ValueError("Category not found")
            existing_link = await self.session.scalar(
                select(MemoryCategory).where(
                    MemoryCategory.memory_object_id == memory_id,
                    MemoryCategory.category_id == category_id,
                )
            )
            if existing_link:
                return
            link = MemoryCategory(
                id=mutation.entity_id,
                memory_object_id=memory_id,
                category_id=category_id,
                source=CategorySource(payload.get("source", "user")),
                hlc=mutation.hlc,
            )
            self.session.add(link)
        elif mutation.operation == "delete" and link:
            await self.session.delete(link)

    async def _apply_thread_mutation(self, user_id: UUID, mutation: SyncMutationInput) -> None:
        payload = mutation.payload
        thread = await self.session.get(Thread, mutation.entity_id)
        if mutation.operation in ("create", "update"):
            if thread:
                if thread.user_id != user_id:
                    raise ValueError("Not authorized")
                if self._hlc_wins(mutation.hlc, thread.hlc):
                    thread.title = payload.get("title", thread.title)
                    thread.status = payload.get("status", thread.status)
                    thread.summary = payload.get("summary", thread.summary)
                    if "primary_entities" in payload:
                        thread.primary_entities = payload.get("primary_entities")
                    thread.hlc = mutation.hlc
                return
            thread = Thread(
                id=mutation.entity_id,
                user_id=user_id,
                title=payload.get("title") or "Thread",
                status=payload.get("status") or "active",
                summary=payload.get("summary"),
                primary_entities=payload.get("primary_entities"),
                hlc=mutation.hlc,
            )
            self.session.add(thread)

    async def _apply_memory_thread_mutation(self, user_id: UUID, mutation: SyncMutationInput) -> None:
        payload = mutation.payload
        link = await self.session.get(MemoryThread, mutation.entity_id)
        memory_id = UUID(payload["memory_object_id"])
        mem = await self.session.get(MemoryObject, memory_id)
        if not mem or mem.user_id != user_id:
            raise ValueError("Not authorized")
        thread_id = UUID(payload["thread_id"])
        thread = await self.session.get(Thread, thread_id)
        if not thread or thread.user_id != user_id:
            raise ValueError("Thread not found")

        if mutation.operation == "create" and not link:
            existing = await self.session.scalar(
                select(MemoryThread).where(
                    MemoryThread.memory_object_id == memory_id,
                    MemoryThread.thread_id == thread_id,
                )
            )
            if existing:
                return
            self.session.add(
                MemoryThread(
                    id=mutation.entity_id,
                    memory_object_id=memory_id,
                    thread_id=thread_id,
                    hlc=mutation.hlc,
                )
            )
        elif mutation.operation == "delete" and link:
            await self.session.delete(link)

    async def _apply_person_mutation(self, user_id: UUID, mutation: SyncMutationInput) -> None:
        from note_db.models import Person

        payload = mutation.payload
        person = await self.session.get(Person, mutation.entity_id)

        if mutation.operation in ("create", "update"):
            if person:
                if self._hlc_wins(mutation.hlc, person.hlc):
                    person.display_name = payload.get("display_name", person.display_name)
                    person.normalized_name = payload.get("normalized_name", person.normalized_name)
                    person.relationship = payload.get("relationship", person.relationship)
                    person.aliases = payload.get("aliases", person.aliases)
                    person.merged_into_id = (
                        UUID(payload["merged_into_id"]) if payload.get("merged_into_id") else person.merged_into_id
                    )
                    person.source = payload.get("source", person.source)
                    person.contact_link_id = payload.get("contact_link_id", person.contact_link_id)
                    person.hlc = mutation.hlc
                return
            person = Person(
                id=mutation.entity_id,
                user_id=user_id,
                display_name=payload["display_name"],
                normalized_name=payload["normalized_name"],
                relationship=payload.get("relationship"),
                aliases=payload.get("aliases"),
                merged_into_id=UUID(payload["merged_into_id"]) if payload.get("merged_into_id") else None,
                source=payload.get("source", "inferred"),
                contact_link_id=payload.get("contact_link_id"),
                hlc=mutation.hlc,
            )
            self.session.add(person)

    async def _ensure_person_exists(
        self,
        user_id: UUID,
        person_id: UUID | None,
        *,
        person_label: str | None,
        hlc: str,
    ) -> UUID | None:
        if not person_id:
            return None

        existing = await self.session.get(Person, person_id)
        if existing:
            if existing.user_id != user_id:
                raise ValueError("Person not authorized")
            return person_id

        label = (person_label or "Unknown").strip()
        normalized = label.lower()
        relationship: str | None = None
        relation_aliases = {
            "mum": "mother",
            "mom": "mother",
            "mother": "mother",
            "dad": "father",
            "father": "father",
            "me": "self",
            "i": "self",
            "myself": "self",
            "self": "self",
        }
        if normalized in relation_aliases:
            relationship = relation_aliases[normalized]
            if relationship == "mother":
                label = "Mum"
            elif relationship == "father":
                label = "Dad"
            elif relationship == "self":
                label = "You"
                normalized = "self"

        person = Person(
            id=person_id,
            user_id=user_id,
            display_name=label,
            normalized_name=normalized,
            relationship=relationship,
            aliases=None,
            merged_into_id=None,
            source="inferred",
            contact_link_id=None,
            hlc=hlc,
        )
        self.session.add(person)
        await self.session.flush()
        return person_id

    async def _create_structured_helper_and_fact(
        self, user_id: UUID, obj: MemoryObject, hlc: str
    ) -> None:
        from uuid import uuid4

        key = obj.structured_title or "fact"
        helper = Helper(
            id=uuid4(),
            memory_object_id=obj.id,
            key=key,
            title=key,
            kind="knowledge",
            value=obj.structured_value,
            source=HelperSource.user,
            status=HelperStatus.accepted,
            hlc=hlc,
        )
        fact = MemoryFact(
            id=uuid4(),
            user_id=user_id,
            key=key,
            value=obj.structured_value or "",
            user_override=True,
            source_memory_ids=[str(obj.id)],
            hlc=hlc,
        )
        self.session.add_all([helper, fact])

    async def _update_cursor(self, user_id: UUID, device_id: UUID) -> None:
        await self._set_cursor(user_id, device_id, datetime.now(UTC).isoformat())

    async def _set_cursor(self, user_id: UUID, device_id: UUID, cursor: str) -> None:
        existing = await self.session.scalar(
            select(SyncCursor).where(
                SyncCursor.user_id == user_id, SyncCursor.device_id == device_id
            )
        )
        if existing:
            existing.cursor = cursor
        else:
            self.session.add(SyncCursor(user_id=user_id, device_id=device_id, cursor=cursor))

    @staticmethod
    def _hlc_wins(incoming: str, existing: str) -> bool:
        return HLC.from_string(incoming) > HLC.from_string(existing)

    @staticmethod
    def _merge_fields(obj: MemoryObject, payload: dict, hlc: str) -> None:
        for field in SYNCABLE_FIELDS["memory_object"]:
            if field in payload and payload[field] is not None:
                if field == "type":
                    setattr(obj, field, MemoryType(payload[field]))
                elif field == "lifecycle":
                    setattr(obj, field, Lifecycle(payload[field]))
                elif field == "visibility":
                    setattr(obj, field, Visibility(payload[field]))
                elif field == "deleted_at" and payload[field]:
                    setattr(obj, field, datetime.fromisoformat(payload[field]))
                else:
                    setattr(obj, field, payload[field])
        obj.hlc = hlc

    @staticmethod
    def _merge_helper_fields(helper: Helper, payload: dict, hlc: str) -> None:
        for field in SYNCABLE_FIELDS["helper"]:
            if field in payload:
                if field == "source":
                    helper.source = HelperSource(payload[field])
                elif field == "status":
                    helper.status = HelperStatus(payload[field])
                elif field == "scheduled_at" and payload[field]:
                    helper.scheduled_at = datetime.fromisoformat(payload[field])
                elif field == "person_id" and payload[field]:
                    helper.person_id = UUID(payload[field])
                elif field == "metadata":
                    helper.helper_metadata = payload[field]
                elif field == "title":
                    helper.title = payload[field] or payload.get("key", helper.key)
                else:
                    setattr(helper, field, payload[field])
        helper.hlc = hlc

    @staticmethod
    def _serialize_memory(m: MemoryObject) -> dict:
        return {
            "id": str(m.id),
            "type": m.type.value,
            "origin": m.origin,
            "content_text": m.content_text,
            "structured_title": m.structured_title,
            "structured_value": m.structured_value,
            "media_uri": m.media_uri,
            "media_type": m.media_type,
            "link_metadata": m.link_metadata,
            "visibility": m.visibility.value,
            "lifecycle": m.lifecycle.value,
            "favorite": m.favorite,
            "ai_state": m.ai_state.value,
            "device_id": str(m.device_id) if m.device_id else None,
            "hlc": m.hlc,
            "version": m.version,
            "deleted_at": m.deleted_at.isoformat() if m.deleted_at else None,
            "created_at": m.created_at.isoformat(),
            "updated_at": m.updated_at.isoformat(),
        }

    @staticmethod
    def _serialize_version(v: MemoryVersion) -> dict:
        return {
            "id": str(v.id),
            "memory_object_id": str(v.memory_object_id),
            "version": v.version,
            "snapshot": v.snapshot,
            "hlc": v.hlc,
            "device_id": str(v.device_id) if v.device_id else None,
            "created_at": v.created_at.isoformat(),
        }

    @staticmethod
    def _serialize_understanding(u: Understanding) -> dict:
        return {
            "id": str(u.id),
            "memory_object_id": str(u.memory_object_id),
            "summary": u.summary,
            "tags": u.tags,
            "topics": u.topics,
            "connections": u.connections,
            "why_it_matters": u.why_it_matters,
            "keyword_summary": u.keyword_summary,
            "superseded_by": str(u.superseded_by) if u.superseded_by else None,
            "created_at": u.created_at.isoformat(),
        }

    @staticmethod
    def _serialize_helper(h: Helper) -> dict:
        return {
            "id": str(h.id),
            "memory_object_id": str(h.memory_object_id),
            "key": h.key,
            "title": h.title,
            "description": h.description,
            "kind": h.kind,
            "proposed_fact_key": h.proposed_fact_key,
            "span_start": h.span_start,
            "span_end": h.span_end,
            "confidence": h.confidence,
            "metadata": h.helper_metadata,
            "tool_name": h.tool_name,
            "value": h.value,
            "person": h.person,
            "person_id": str(h.person_id) if h.person_id else None,
            "semantic_type": h.semantic_type,
            "source": h.source.value,
            "status": h.status.value,
            "scheduled_at": h.scheduled_at.isoformat() if h.scheduled_at else None,
            "hlc": h.hlc,
            "created_at": h.created_at.isoformat(),
            "updated_at": h.updated_at.isoformat(),
        }

    @staticmethod
    def _serialize_fact(f: MemoryFact) -> dict:
        return {
            "id": str(f.id),
            "key": f.key,
            "display_label": f.display_label,
            "fact_type": f.fact_type,
            "value": f.value,
            "person": f.person,
            "person_id": str(f.person_id) if f.person_id else None,
            "semantic_type": f.semantic_type,
            "user_override": f.user_override,
            "source_memory_ids": f.source_memory_ids,
            "mention_count": f.mention_count or 1,
            "last_reaffirmed_at": f.last_reaffirmed_at.isoformat() if f.last_reaffirmed_at else None,
            "expected_volatility": f.expected_volatility or "slow",
            "expires_at": f.expires_at.isoformat() if f.expires_at else None,
            "hlc": f.hlc,
            "created_at": f.created_at.isoformat(),
            "updated_at": f.updated_at.isoformat(),
        }

    @staticmethod
    def _serialize_thread(t: Thread) -> dict:
        return {
            "id": str(t.id),
            "title": t.title,
            "status": t.status,
            "summary": t.summary,
            "primary_entities": t.primary_entities,
            "first_memory_at": t.first_memory_at.isoformat() if t.first_memory_at else None,
            "last_memory_at": t.last_memory_at.isoformat() if t.last_memory_at else None,
            "hlc": t.hlc,
            "created_at": t.created_at.isoformat(),
            "updated_at": t.updated_at.isoformat(),
        }

    @staticmethod
    def _serialize_memory_thread(mt: MemoryThread) -> dict:
        return {
            "id": str(mt.id),
            "memory_object_id": str(mt.memory_object_id),
            "thread_id": str(mt.thread_id),
            "hlc": mt.hlc,
            "created_at": mt.created_at.isoformat(),
        }

    @staticmethod
    def _serialize_person(p: Person) -> dict:
        return {
            "id": str(p.id),
            "display_name": p.display_name,
            "normalized_name": p.normalized_name,
            "relationship": p.relationship,
            "aliases": p.aliases,
            "merged_into_id": str(p.merged_into_id) if p.merged_into_id else None,
            "source": p.source,
            "contact_link_id": p.contact_link_id,
            "hlc": p.hlc,
            "created_at": p.created_at.isoformat(),
            "updated_at": p.updated_at.isoformat(),
        }

    @staticmethod
    def _serialize_category(c: Category) -> dict:
        return {
            "id": str(c.id),
            "name": c.name,
            "color": c.color,
            "source": c.source.value,
            "hlc": c.hlc,
            "created_at": c.created_at.isoformat(),
        }

    @staticmethod
    def _serialize_memory_category(mc: MemoryCategory) -> dict:
        return {
            "id": str(mc.id),
            "memory_object_id": str(mc.memory_object_id),
            "category_id": str(mc.category_id),
            "source": mc.source.value,
            "hlc": mc.hlc,
            "created_at": mc.created_at.isoformat(),
        }
