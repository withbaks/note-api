"""Build and maintain search_documents index for memories."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from uuid import UUID, uuid4

from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from note_core.circuit import circuit_is_open, record_llm_failure, record_llm_success
from note_core.config import get_settings
from note_db.models import (
    Entity,
    Friendship,
    MemoryFact,
    MemoryObject,
    Person,
    SearchDocument,
    Understanding,
    User,
)

CHUNK_SIZE = 2000
CHUNK_OVERLAP = 200


@dataclass
class SearchDocDraft:
    doc_type: str
    text: str
    metadata: dict | None = None


def _chunk_text(text: str, *, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    cleaned = text.strip()
    if not cleaned:
        return []
    if len(cleaned) <= chunk_size:
        return [cleaned]

    chunks: list[str] = []
    start = 0
    while start < len(cleaned):
        end = min(len(cleaned), start + chunk_size)
        chunks.append(cleaned[start:end])
        if end >= len(cleaned):
            break
        start = max(0, end - overlap)
    return chunks


def _bookmark_link_text(obj: MemoryObject) -> str:
    parts: list[str] = []
    meta = obj.link_metadata if isinstance(obj.link_metadata, dict) else {}
    for key in ("title", "description", "author", "site_name"):
        value = meta.get(key)
        if value:
            parts.append(str(value))
    extracted = meta.get("extracted_text")
    if extracted:
        parts.append(str(extracted)[:4000])
    if obj.structured_title:
        parts.append(obj.structured_title)
    if obj.structured_value:
        parts.append(obj.structured_value)
    if obj.content_text:
        parts.append(obj.content_text)
    return "\n".join(p for p in parts if p)


def _memory_preview(obj: MemoryObject) -> str:
    if obj.type.value == "bookmark":
        bookmark_text = _bookmark_link_text(obj)
        if bookmark_text:
            return bookmark_text.strip()
    if obj.content_text:
        return obj.content_text.strip()
    if obj.structured_title or obj.structured_value:
        return f"{obj.structured_title or ''}: {obj.structured_value or ''}".strip(": ").strip()
    return ""


def _chunk_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


class SearchIndexService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.settings = get_settings()

    async def index_memory(self, memory_object_id: UUID) -> int:
        obj = await self.session.get(MemoryObject, memory_object_id)
        if not obj:
            return 0

        drafts = await self._build_drafts(obj)
        if not drafts:
            await self.session.execute(
                delete(SearchDocument).where(SearchDocument.memory_object_id == memory_object_id)
            )
            return 0

        for draft in drafts:
            draft.metadata = {**(draft.metadata or {}), "chunk_hash": _chunk_hash(draft.text)}

        existing = (
            await self.session.scalars(
                select(SearchDocument).where(SearchDocument.memory_object_id == memory_object_id)
            )
        ).all()
        existing_by_hash: dict[str, SearchDocument] = {}
        for doc in existing:
            h = (doc.doc_metadata or {}).get("chunk_hash")
            if h:
                existing_by_hash[h] = doc

        new_hashes = {(d.metadata or {}).get("chunk_hash") for d in drafts}
        for doc in existing:
            h = (doc.doc_metadata or {}).get("chunk_hash")
            if not h or h not in new_hashes:
                await self.session.delete(doc)

        to_embed: list[SearchDocDraft] = []
        reuse_embeddings: list[tuple[SearchDocDraft, list[float] | None]] = []
        for draft in drafts:
            h = (draft.metadata or {}).get("chunk_hash")
            if h and h in existing_by_hash and existing_by_hash[h].embedding is not None:
                reuse_embeddings.append((draft, existing_by_hash[h].embedding))
            else:
                to_embed.append(draft)

        new_embeddings = await self._embed_texts([d.text for d in to_embed])

        embed_idx = 0
        for draft, embedding in reuse_embeddings:
            h = (draft.metadata or {}).get("chunk_hash")
            existing_doc = existing_by_hash.get(h or "")
            if existing_doc:
                existing_doc.text = draft.text
                existing_doc.doc_type = draft.doc_type
                existing_doc.doc_metadata = draft.metadata
            else:
                self.session.add(
                    SearchDocument(
                        id=uuid4(),
                        user_id=obj.user_id,
                        memory_object_id=obj.id,
                        doc_type=draft.doc_type,
                        text=draft.text,
                        embedding=embedding,
                        doc_metadata=draft.metadata,
                    )
                )

        for draft in to_embed:
            embedding = new_embeddings[embed_idx] if embed_idx < len(new_embeddings) else None
            embed_idx += 1
            h = (draft.metadata or {}).get("chunk_hash")
            existing_doc = existing_by_hash.get(h or "") if h else None
            if existing_doc:
                existing_doc.text = draft.text
                existing_doc.doc_type = draft.doc_type
                existing_doc.doc_metadata = draft.metadata
                existing_doc.embedding = embedding
            else:
                self.session.add(
                    SearchDocument(
                        id=uuid4(),
                        user_id=obj.user_id,
                        memory_object_id=obj.id,
                        doc_type=draft.doc_type,
                        text=draft.text,
                        embedding=embedding,
                        doc_metadata=draft.metadata,
                    )
                )

        await self.session.flush()
        return len(drafts)

    async def index_user_profile(self, user_id: UUID) -> int:
        """Index standalone docs for people, facts, and friendship rollups."""
        await self.session.execute(
            delete(SearchDocument).where(
                SearchDocument.user_id == user_id,
                SearchDocument.memory_object_id.is_(None),
            )
        )

        drafts: list[SearchDocDraft] = []

        people = (
            await self.session.scalars(
                select(Person).where(
                    Person.user_id == user_id,
                    Person.merged_into_id.is_(None),
                )
            )
        ).all()
        for person in people:
            rel = f" ({person.relationship})" if person.relationship else ""
            text = f"{person.display_name}{rel}"
            drafts.append(
                SearchDocDraft(
                    doc_type="person",
                    text=text,
                    metadata={"person_id": str(person.id)},
                )
            )

        facts = (
            await self.session.scalars(select(MemoryFact).where(MemoryFact.user_id == user_id))
        ).all()
        for fact in facts:
            label = fact.display_label or fact.key.replace("_", " ")
            who = f"{fact.person} " if fact.person else ""
            drafts.append(
                SearchDocDraft(
                    doc_type="fact",
                    text=f"{who}{label}: {fact.value}",
                    metadata={"fact_id": str(fact.id), "fact_key": fact.key},
                )
            )

        friendships = (
            await self.session.scalars(
                select(Friendship).where(
                    Friendship.status == "accepted",
                    Friendship.user_id == user_id,
                )
            )
        ).all()
        friend_names: list[str] = []
        for fs in friendships:
            user = await self.session.get(User, fs.friend_id)
            if user:
                friend_names.append(user.display_name or user.username)
        if friend_names:
            drafts.append(
                SearchDocDraft(
                    doc_type="friendship_rollup",
                    text=f"You have {len(friend_names)} friends: {', '.join(friend_names)}",
                    metadata={"friend_count": len(friend_names)},
                )
            )

        if not drafts:
            return 0

        texts = [d.text for d in drafts]
        embeddings = await self._embed_texts(texts)
        for draft, embedding in zip(drafts, embeddings, strict=True):
            self.session.add(
                SearchDocument(
                    id=uuid4(),
                    user_id=user_id,
                    memory_object_id=None,
                    doc_type=draft.doc_type,
                    text=draft.text,
                    embedding=embedding,
                    doc_metadata=draft.metadata,
                )
            )
        await self.session.flush()
        return len(drafts)

    async def index_fact(self, user_id: UUID, fact_id: UUID) -> None:
        await self.index_user_profile(user_id)

    async def backfill_user(self, user_id: UUID, *, limit: int = 50) -> int:
        rows = (
            await self.session.scalars(
                select(MemoryObject.id)
                .where(MemoryObject.user_id == user_id)
                .order_by(MemoryObject.updated_at.desc())
                .limit(limit)
            )
        ).all()

        indexed = 0
        for memory_id in rows:
            indexed += await self.index_memory(memory_id)
        indexed += await self.index_user_profile(user_id)
        return indexed

    async def backfill_batch(self, *, limit: int = 25) -> int:
        """Index memories that have no search_documents rows yet."""
        rows = (
            await self.session.execute(
                text("""
            SELECT mo.id
            FROM memory_objects mo
            LEFT JOIN search_documents sd ON sd.memory_object_id = mo.id
            WHERE mo.lifecycle != 'deleted'
              AND sd.id IS NULL
            ORDER BY mo.updated_at DESC
            LIMIT :limit
        """),
                {"limit": limit},
            )
        ).all()
        indexed = 0
        for row in rows:
            indexed += await self.index_memory(UUID(str(row.id)))
        return indexed

    async def _build_drafts(self, obj: MemoryObject) -> list[SearchDocDraft]:
        drafts: list[SearchDocDraft] = []

        memory_text = _memory_preview(obj)
        if memory_text:
            chunks = _chunk_text(memory_text)
            for index, chunk in enumerate(chunks):
                drafts.append(
                    SearchDocDraft(
                        doc_type="chunk" if len(chunks) > 1 else "content",
                        text=chunk,
                        metadata={"chunk_index": index, "chunk_count": len(chunks)},
                    )
                )

        understanding = await self.session.scalar(
            select(Understanding).where(
                Understanding.memory_object_id == obj.id,
                Understanding.superseded_by.is_(None),
            )
        )
        if understanding:
            summary_parts: list[str] = []
            if understanding.summary:
                summary_parts.append(understanding.summary.strip())
            if understanding.keyword_summary:
                summary_parts.append(understanding.keyword_summary.strip())
            for field in (understanding.tags, understanding.topics):
                if isinstance(field, list):
                    summary_parts.append(" ".join(str(v) for v in field))
            summary_text = "\n".join(p for p in summary_parts if p)
            if summary_text:
                raw = understanding.raw if isinstance(understanding.raw, dict) else {}
                metadata = {
                    "temporal_refs": raw.get("temporal_refs") or [],
                    "people_refs": raw.get("people_refs") or [],
                    "place_refs": raw.get("place_refs") or [],
                    "intent_tags": raw.get("intent_tags") or [],
                }
                drafts.append(SearchDocDraft(doc_type="summary", text=summary_text, metadata=metadata))

        entities = (
            await self.session.scalars(select(Entity).where(Entity.memory_object_id == obj.id))
        ).all()
        for entity in entities:
            value = (entity.value or "").strip()
            if value:
                drafts.append(
                    SearchDocDraft(
                        doc_type="entity",
                        text=value,
                        metadata={"entity_type": entity.entity_type},
                    )
                )

        facts = (
            await self.session.scalars(
                select(MemoryFact).where(MemoryFact.user_id == obj.user_id)
            )
        ).all()
        for fact in facts:
            source_ids = [str(s) for s in (fact.source_memory_ids or [])]
            if str(obj.id) not in source_ids:
                continue
            key_label = fact.key.replace("is_", "").replace("_", " ")
            preview = f"{fact.person}: {fact.value}" if fact.person else f"{key_label}: {fact.value}"
            drafts.append(
                SearchDocDraft(
                    doc_type="fact",
                    text=preview,
                    metadata={"fact_key": fact.key},
                )
            )

        return [d for d in drafts if d.text.strip()]

    async def _embed_texts(self, texts: list[str]) -> list[list[float] | None]:
        if not texts:
            return []
        if not self.settings.openai_api_key or circuit_is_open():
            return [None for _ in texts]

        import asyncio

        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=self.settings.openai_api_key, timeout=30.0)
        try:
            response = await asyncio.wait_for(
                client.embeddings.create(model="text-embedding-3-small", input=texts),
                timeout=30.0,
            )
            record_llm_success()
            return [item.embedding for item in response.data]
        except Exception:
            record_llm_failure()
            return [None for _ in texts]

