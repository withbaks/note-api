from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import and_, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from note_core.config import get_settings
from note_db.models import Entity, Lifecycle, MemoryFact, MemoryObject, Understanding, User
from note_search.query_understanding import (
    expand_search_queries,
    extract_search_terms,
    looks_like_question,
)

SEMANTIC_MIN_SCORE = 0.30
SEMANTIC_RELATIVE_GAP = 0.08
QUESTION_MIN_TOP_SCORE = 0.28


class SearchResult(BaseModel):
    memory_id: UUID | None = None
    fact_id: UUID | None = None
    person_id: UUID | None = None
    friendship_id: UUID | None = None
    source_type: str = "memory"
    content_preview: str
    match_type: str
    score: float = 1.0
    tags: list[str] | None = None


class AIQueryResponse(BaseModel):
    answer: str
    sources: list[SearchResult]


class SearchService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.settings = get_settings()

    async def keyword_search(self, user_id: UUID, query: str, limit: int = 20) -> list[SearchResult]:
        terms = extract_search_terms(query)
        if not terms:
            terms = [query.strip().lower()] if query.strip() else []
        if not terms:
            return []

        fts_results = await self._fts_memory_search(user_id, query, limit)
        if fts_results:
            return fts_results

        clauses = []
        for term in terms:
            pattern = f"%{term}%"
            clauses.extend(
                [
                    MemoryObject.content_text.ilike(pattern),
                    MemoryObject.structured_title.ilike(pattern),
                    MemoryObject.structured_value.ilike(pattern),
                    Understanding.keyword_summary.ilike(pattern),
                    Understanding.summary.ilike(pattern),
                ]
            )

        stmt = (
            select(MemoryObject)
            .outerjoin(
                Understanding,
                and_(
                    Understanding.memory_object_id == MemoryObject.id,
                    Understanding.superseded_by.is_(None),
                ),
            )
            .where(
                MemoryObject.user_id == user_id,
                MemoryObject.lifecycle != Lifecycle.deleted,
                or_(*clauses),
            )
            .distinct()
            .limit(limit)
        )
        memories = (await self.session.scalars(stmt)).all()
        results = []
        for m in memories:
            preview = m.content_text or f"{m.structured_title}: {m.structured_value}"
            results.append(
                SearchResult(
                    memory_id=m.id,
                    content_preview=preview[:200] if preview else "",
                    match_type="keyword",
                    score=0.52,
                )
            )
        return results

    async def _fts_memory_search(self, user_id: UUID, query: str, limit: int) -> list[SearchResult]:
        stmt = text("""
            SELECT mo.id, mo.content_text, mo.structured_title, mo.structured_value,
                   ts_rank(
                     to_tsvector(
                       'english',
                       coalesce(mo.content_text, '') || ' ' ||
                       coalesce(mo.structured_title, '') || ' ' ||
                       coalesce(mo.structured_value, '')
                     ),
                     plainto_tsquery('english', :q)
                   ) AS score
            FROM memory_objects mo
            WHERE mo.user_id = :user_id
              AND mo.lifecycle != 'deleted'
              AND to_tsvector(
                    'english',
                    coalesce(mo.content_text, '') || ' ' ||
                    coalesce(mo.structured_title, '') || ' ' ||
                    coalesce(mo.structured_value, '')
                  ) @@ plainto_tsquery('english', :q)
            ORDER BY score DESC
            LIMIT :limit
        """)
        try:
            rows = (
                await self.session.execute(
                    stmt,
                    {"user_id": str(user_id), "q": query, "limit": limit},
                )
            ).all()
        except Exception:
            return []

        if not rows:
            return []

        results = []
        for row in rows:
            preview = row.content_text or f"{row.structured_title}: {row.structured_value}"
            rank = float(row.score or 0)
            results.append(
                SearchResult(
                    memory_id=UUID(str(row.id)),
                    content_preview=preview[:200] if preview else "",
                    match_type="fts",
                    score=min(0.95, 0.45 + rank * 2.5),
                )
            )
        return results

    async def fact_search(self, user_id: UUID, query: str, limit: int = 15) -> list[SearchResult]:
        terms = extract_search_terms(query)
        if not terms:
            terms = [query.strip().lower()] if query.strip() else []
        if not terms:
            return []

        clauses = []
        for term in terms:
            pattern = f"%{term}%"
            clauses.extend(
                [
                    MemoryFact.key.ilike(pattern),
                    MemoryFact.value.ilike(pattern),
                    MemoryFact.person.ilike(pattern),
                ]
            )

        facts = (
            await self.session.scalars(
                select(MemoryFact).where(MemoryFact.user_id == user_id, or_(*clauses)).limit(limit)
            )
        ).all()

        results: list[SearchResult] = []
        for fact in facts:
            memory_id = None
            if fact.source_memory_ids:
                try:
                    memory_id = fact.source_memory_ids[0]
                except (IndexError, TypeError):
                    memory_id = None
            key_label = fact.key.replace("is_", "").replace("_", " ")
            preview = (
                f"{fact.person}: {fact.value}" if fact.person else f"{key_label}: {fact.value}"
            )
            hay = f"{fact.key} {fact.value} {fact.person or ''}".lower()
            matched = sum(1 for t in terms if t in hay)
            score = 0.42 + min(0.35, matched * 0.12)
            results.append(
                SearchResult(
                    memory_id=memory_id,
                    fact_id=fact.id,
                    content_preview=preview[:200],
                    match_type="fact",
                    score=score,
                )
            )
        return results

    async def understanding_search(self, user_id: UUID, query: str, limit: int = 10) -> list[SearchResult]:
        terms = extract_search_terms(query)
        if not terms:
            import re

            terms = [w for w in re.findall(r"[a-z0-9]+", query.lower()) if len(w) > 2][:5]
        if not terms:
            return []

        clauses = []
        for term in terms:
            pattern = f"%{term}%"
            clauses.extend(
                [
                    Understanding.keyword_summary.ilike(pattern),
                    Understanding.summary.ilike(pattern),
                ]
            )

        stmt = (
            select(MemoryObject)
            .join(
                Understanding,
                and_(
                    Understanding.memory_object_id == MemoryObject.id,
                    Understanding.superseded_by.is_(None),
                ),
            )
            .where(
                MemoryObject.user_id == user_id,
                MemoryObject.lifecycle != Lifecycle.deleted,
                or_(*clauses),
            )
            .distinct()
            .limit(limit)
        )
        memories = (await self.session.scalars(stmt)).all()
        results: list[SearchResult] = []
        for m in memories:
            preview = m.content_text or f"{m.structured_title}: {m.structured_value}"
            results.append(
                SearchResult(
                    memory_id=m.id,
                    content_preview=preview[:200] if preview else "",
                    match_type="understanding",
                    score=0.58,
                )
            )
        return results

    async def entity_search(self, user_id: UUID, query: str, limit: int = 20) -> list[SearchResult]:
        terms = extract_search_terms(query) or [query.strip().lower()]
        clauses = [Entity.value.ilike(f"%{term}%") for term in terms if term]
        if not clauses:
            return []

        stmt = (
            select(Entity, MemoryObject)
            .join(MemoryObject, Entity.memory_object_id == MemoryObject.id)
            .where(
                Entity.user_id == user_id,
                MemoryObject.lifecycle != Lifecycle.deleted,
                or_(*clauses),
            )
            .limit(limit)
        )
        rows = (await self.session.execute(stmt)).all()
        results = []
        for entity, mem in rows:
            preview = mem.content_text or f"{mem.structured_title}: {mem.structured_value}"
            results.append(
                SearchResult(
                    memory_id=mem.id,
                    content_preview=preview[:200] if preview else entity.value,
                    match_type=f"entity:{entity.entity_type}",
                    score=0.48,
                )
            )
        return results

    async def semantic_search(self, user_id: UUID, query: str, limit: int = 10) -> list[SearchResult]:
        if not self.settings.openai_api_key:
            return await self.keyword_search(user_id, query, limit)

        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=self.settings.openai_api_key)
        emb_response = await client.embeddings.create(
            model="text-embedding-3-small",
            input=query,
        )
        query_embedding = emb_response.data[0].embedding

        stmt = text("""
            SELECT mo.id, mo.content_text, mo.structured_title, mo.structured_value,
                   1 - (e.embedding <=> :embedding) AS score
            FROM entities e
            JOIN memory_objects mo ON mo.id = e.memory_object_id
            WHERE e.user_id = :user_id
              AND e.embedding IS NOT NULL
              AND mo.lifecycle != 'deleted'
            ORDER BY e.embedding <=> :embedding
            LIMIT :limit
        """)
        try:
            rows = (
                await self.session.execute(
                    stmt,
                    {"embedding": str(query_embedding), "user_id": str(user_id), "limit": limit * 3},
                )
            ).all()
        except Exception:
            return await self.keyword_search(user_id, query, limit)

        deduped: dict[str, SearchResult] = {}
        for row in rows:
            score = float(row.score or 0)
            if score < SEMANTIC_MIN_SCORE:
                continue
            mem_id = str(row.id)
            preview = row.content_text or f"{row.structured_title}: {row.structured_value}"
            existing = deduped.get(mem_id)
            if existing and existing.score >= score:
                continue
            deduped[mem_id] = SearchResult(
                memory_id=UUID(mem_id),
                content_preview=preview[:200] if preview else "",
                match_type="semantic",
                score=score,
            )

        results = sorted(deduped.values(), key=lambda r: r.score, reverse=True)
        if not results:
            return []

        top = results[0].score
        trimmed = [r for r in results if r.score >= max(SEMANTIC_MIN_SCORE, top - SEMANTIC_RELATIVE_GAP)]
        return trimmed[:limit]

    @staticmethod
    def _source_key(result: SearchResult) -> str:
        if result.fact_id:
            return f"fact:{result.fact_id}"
        if result.memory_id:
            return f"memory:{result.memory_id}"
        return result.content_preview

    @staticmethod
    def _merge_ranked_results(results: list[SearchResult]) -> list[SearchResult]:
        best: dict[str, SearchResult] = {}
        for result in results:
            key = SearchService._source_key(result)
            existing = best.get(key)
            if not existing or result.score > existing.score:
                best[key] = result
        return sorted(best.values(), key=lambda r: r.score, reverse=True)

    @staticmethod
    def _filter_for_question(results: list[SearchResult]) -> list[SearchResult]:
        if not results:
            return []
        top = results[0].score
        if top < QUESTION_MIN_TOP_SCORE:
            strong = [r for r in results if r.match_type in {"fts", "keyword", "understanding", "fact"}]
            if not strong:
                return []
            results = strong
            top = results[0].score
            if top < 0.42:
                return []
        return [r for r in results if r.score >= max(QUESTION_MIN_TOP_SCORE, top - SEMANTIC_RELATIVE_GAP)]

    async def hybrid_retrieve(
        self,
        user_id: UUID,
        query: str,
        *,
        limit: int = 20,
        question: bool | None = None,
    ) -> list[SearchResult]:
        is_question = looks_like_question(query) if question is None else question
        queries = expand_search_queries(query)

        pooled: list[SearchResult] = []
        for q in queries:
            semantic = await self.semantic_search(user_id, q, 12)
            understanding = await self.understanding_search(user_id, q, 8)
            facts = await self.fact_search(user_id, q, 10)
            keyword = await self.keyword_search(user_id, q, 8)
            entity = await self.entity_search(user_id, q, 8) if not is_question else []
            pooled.extend(semantic + understanding + keyword + facts + entity)

        ranked = self._merge_ranked_results(pooled)
        if is_question:
            ranked = self._filter_for_question(ranked)
        return ranked[:limit]

    async def ai_query(self, user_id: UUID, query: str) -> AIQueryResponse:
        from note_search.smart_search import SmartSearchPipeline

        result = await SmartSearchPipeline(self.session).smart_search(user_id, query)
        return AIQueryResponse(answer=result.answer, sources=result.sources)

    async def smart_search(self, user_id: UUID, query: str):
        from note_search.smart_search import SmartSearchPipeline

        return await SmartSearchPipeline(self.session).smart_search(user_id, query)
