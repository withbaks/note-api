"""Smart adaptive search pipeline."""

from __future__ import annotations

import json
import time
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from note_core.config import get_settings
from note_db.models import Understanding, User
from note_search.query_router import QueryClass, route_query
from note_search.query_understanding import expand_search_queries
from note_search.relation_expand import expand_related_memories
from note_search.retrieval_boost import (
    apply_retrieval_boosts,
    extract_must_include_heuristic,
    extract_temporal_heuristic,
)
from note_search.rrf import reciprocal_rank_fusion
from note_search.service import (
    SEMANTIC_MIN_SCORE,
    SEMANTIC_RELATIVE_GAP,
    SearchResult,
    SearchService,
)
from note_search.structured_query import QueryIntent, StructuredQueryService, classify_intent
from note_search.topic_expand import expand_topic_cooccur

LATENCY_BUDGET_MS = 1500
RERANK_MIN_CANDIDATES = 5
KEYWORD_FALLTHROUGH_MIN = 3


class SmartSearchResponse(BaseModel):
    answer: str
    sources: list[SearchResult]
    pipeline: str
    latency_ms: int


class SmartSearchPipeline:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.settings = get_settings()
        self.base = SearchService(session)

    async def smart_search(self, user_id: UUID, query: str) -> SmartSearchResponse:
        started = time.perf_counter()
        user = await self.session.get(User, user_id)
        if user and not user.ai_use_notes_context:
            return SmartSearchResponse(
                answer="AI context from notes is disabled in settings.",
                sources=[],
                pipeline="disabled",
                latency_ms=0,
            )

        routed = await route_query(query)
        intent = classify_intent(query)

        if intent != QueryIntent.memory_recall:
            structured = await StructuredQueryService(self.session).answer(
                user_id, query, intent
            )
            if structured:
                latency_ms = int((time.perf_counter() - started) * 1000)
                return SmartSearchResponse(
                    answer=structured.answer,
                    sources=structured.sources,
                    pipeline=structured.pipeline,
                    latency_ms=latency_ms,
                )

        must_include = list(
            dict.fromkeys(
                [*(routed.must_include or []), *extract_must_include_heuristic(query)]
            )
        )
        temporal_filter = routed.temporal_filter or extract_temporal_heuristic(query)

        queries = list(
            dict.fromkeys(
                q for base in routed.search_queries for q in expand_search_queries(base)
            )
        )

        used_semantic = False
        if routed.query_class == QueryClass.keyword:
            combined = await self._keyword_pipeline(user_id, queries)
            pipeline = "keyword"
            # H3: low lexical recall → fall through to full hybrid channels
            if len(combined) < KEYWORD_FALLTHROUGH_MIN:
                combined = await self._semantic_pipeline(user_id, queries, QueryClass.semantic)
                pipeline = "keyword+semantic"
                used_semantic = True
        else:
            combined = await self._semantic_pipeline(user_id, queries, routed.query_class)
            pipeline = routed.query_class.value
            used_semantic = True

        # H2: person/fact one-hop expansion as an extra RRF channel
        relation_hits = await expand_related_memories(
            self.session,
            user_id,
            query,
            must_include=must_include,
            seed_results=combined,
        )
        if relation_hits:
            combined = reciprocal_rank_fusion(
                [combined, relation_hits],
                key_fn=self.base._source_key,
            )[:20]
            pipeline = f"{pipeline}+relation"

        # H4: topic co-occurrence expansion
        topic_hits = await expand_topic_cooccur(self.session, user_id, combined)
        if topic_hits:
            combined = reciprocal_rank_fusion(
                [combined, topic_hits],
                key_fn=self.base._source_key,
            )[:20]
            pipeline = f"{pipeline}+topic"

        # H1: must_include / temporal / people_refs boosts
        combined = await apply_retrieval_boosts(
            self.session,
            combined,
            must_include=must_include,
            temporal_filter=temporal_filter,
        )

        elapsed = int((time.perf_counter() - started) * 1000)
        if (
            (used_semantic or routed.query_class != QueryClass.keyword)
            and len(combined) > RERANK_MIN_CANDIDATES
            and elapsed < LATENCY_BUDGET_MS
            and self.settings.openai_api_key
        ):
            combined = await self._rerank(query, combined)
            pipeline = f"{pipeline}+rerank"

        answer, sources = await self._answer(user_id, query, combined)
        latency_ms = int((time.perf_counter() - started) * 1000)
        return SmartSearchResponse(
            answer=answer,
            sources=sources,
            pipeline=pipeline,
            latency_ms=latency_ms,
        )

    async def _keyword_pipeline(self, user_id: UUID, queries: list[str]) -> list[SearchResult]:
        pooled: list[SearchResult] = []
        for q in queries:
            pooled.extend(await self.base.keyword_search(user_id, q, 10))
            pooled.extend(await self.search_documents_fts(user_id, q, 10))
            pooled.extend(await self.base.fact_search(user_id, q, 8))
        return self.base._merge_ranked_results(pooled)[:20]

    async def _semantic_pipeline(
        self, user_id: UUID, queries: list[str], query_class: QueryClass
    ) -> list[SearchResult]:
        lists: list[list[SearchResult]] = []
        for q in queries:
            vector = await self.search_documents_vector(user_id, q, 15)
            fts = await self.search_documents_fts(user_id, q, 10)
            keyword = await self.base.keyword_search(user_id, q, 8)
            understanding = await self.base.understanding_search(user_id, q, 8)
            facts = await self.base.fact_search(user_id, q, 8)
            entity = await self.base.entity_search(user_id, q, 6)
            lists.extend([vector, fts, keyword, understanding, facts, entity])

        if not lists:
            return []

        merged = reciprocal_rank_fusion(
            lists,
            key_fn=self.base._source_key,
        )
        return merged[:20]

    async def search_documents_vector(self, user_id: UUID, query: str, limit: int = 15) -> list[SearchResult]:
        if not self.settings.openai_api_key:
            return []

        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=self.settings.openai_api_key)
        emb = await client.embeddings.create(model="text-embedding-3-small", input=query)
        query_embedding = emb.data[0].embedding

        stmt = text("""
            SELECT sd.id, sd.memory_object_id, sd.text, sd.doc_type,
                   1 - (sd.embedding <=> :embedding) AS score,
                   mo.content_text, mo.structured_title, mo.structured_value
            FROM search_documents sd
            JOIN memory_objects mo ON mo.id = sd.memory_object_id
            WHERE sd.user_id = :user_id
              AND sd.embedding IS NOT NULL
              AND mo.lifecycle != 'deleted'
            ORDER BY sd.embedding <=> :embedding
            LIMIT :limit
        """)
        try:
            rows = (
                await self.session.execute(
                    stmt,
                    {
                        "embedding": str(query_embedding),
                        "user_id": str(user_id),
                        "limit": limit * 2,
                    },
                )
            ).all()
        except Exception:
            return await self.base.semantic_search(user_id, query, limit)

        deduped: dict[str, SearchResult] = {}
        for row in rows:
            score = float(row.score or 0)
            if score < SEMANTIC_MIN_SCORE:
                continue
            mem_id = str(row.memory_object_id)
            preview = row.text or row.content_text or f"{row.structured_title}: {row.structured_value}"
            existing = deduped.get(mem_id)
            if existing and existing.score >= score:
                continue
            deduped[mem_id] = SearchResult(
                memory_id=UUID(mem_id),
                content_preview=(preview or "")[:200],
                match_type=f"doc_vector:{row.doc_type}",
                score=score,
            )

        results = sorted(deduped.values(), key=lambda r: r.score, reverse=True)
        if not results:
            return []
        top = results[0].score
        return [r for r in results if r.score >= max(SEMANTIC_MIN_SCORE, top - SEMANTIC_RELATIVE_GAP)][:limit]

    async def search_documents_fts(self, user_id: UUID, query: str, limit: int = 10) -> list[SearchResult]:
        stmt = text("""
            SELECT sd.memory_object_id, sd.text, sd.doc_type,
                   ts_rank(
                     to_tsvector('english', coalesce(sd.text, '')),
                     plainto_tsquery('english', :q)
                   ) AS score
            FROM search_documents sd
            JOIN memory_objects mo ON mo.id = sd.memory_object_id
            WHERE sd.user_id = :user_id
              AND mo.lifecycle != 'deleted'
              AND to_tsvector('english', coalesce(sd.text, '')) @@ plainto_tsquery('english', :q)
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

        deduped: dict[str, SearchResult] = {}
        for row in rows:
            mem_id = str(row.memory_object_id)
            rank = float(row.score or 0)
            score = min(0.95, 0.45 + rank * 2.5)
            preview = row.text or ""
            existing = deduped.get(mem_id)
            if existing and existing.score >= score:
                continue
            deduped[mem_id] = SearchResult(
                memory_id=UUID(mem_id),
                content_preview=preview[:200],
                match_type=f"doc_fts:{row.doc_type}",
                score=score,
            )
        return sorted(deduped.values(), key=lambda r: r.score, reverse=True)

    async def _rerank(self, query: str, candidates: list[SearchResult]) -> list[SearchResult]:
        top = candidates[:20]
        lines = [f"[{i}] {r.content_preview}" for i, r in enumerate(top)]
        prompt = f"""Rank these saved note snippets by relevance to the user's query.

Query: {query}

Snippets:
{chr(10).join(lines)}

Return JSON only: {{"ranked_indices": [0, 2, 1]}}
Include only relevant indices; use [] if none are relevant."""

        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=self.settings.openai_api_key)
        try:
            response = await client.chat.completions.create(
                model=self.settings.ai_model,
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
                max_tokens=200,
            )
            parsed = json.loads(response.choices[0].message.content or "{}")
            indices = parsed.get("ranked_indices") or []
            reranked: list[SearchResult] = []
            seen: set[int] = set()
            for raw_idx in indices:
                idx = int(raw_idx)
                if idx in seen or idx < 0 or idx >= len(top):
                    continue
                seen.add(idx)
                reranked.append(top[idx])
            if reranked:
                return reranked
        except Exception:
            pass
        return candidates

    async def _answer(
        self, user_id: UUID, query: str, combined: list[SearchResult]
    ) -> tuple[str, list[SearchResult]]:
        memory_sources = [r for r in combined if r.memory_id and not r.fact_id]
        fact_sources = [r for r in combined if r.fact_id]

        if not memory_sources and not fact_sources:
            return (
                "I couldn't find anything relevant in your memories for that question.",
                [],
            )

        context_lines: list[str] = []
        indexed: list[SearchResult] = []

        for r in memory_sources[:12]:
            idx = len(indexed)
            context_lines.append(f"[{idx}] {r.content_preview}")
            indexed.append(r)

        for r in fact_sources[:8]:
            idx = len(indexed)
            context_lines.append(f"[{idx}] (fact) {r.content_preview}")
            indexed.append(r)

        if memory_sources:
            mem_ids = [r.memory_id for r in memory_sources[:6] if r.memory_id]
            if mem_ids:
                understandings = (
                    await self.session.scalars(
                        select(Understanding).where(
                            Understanding.memory_object_id.in_(mem_ids),
                            Understanding.superseded_by.is_(None),
                        )
                    )
                ).all()
                for u in understandings:
                    if u.summary:
                        context_lines.append(f"(context) Summary: {u.summary}")
                    if u.keyword_summary:
                        context_lines.append(f"(context) Keywords: {u.keyword_summary}")

        context = "\n".join(context_lines)

        if not self.settings.openai_api_key:
            return f"Based on your memories:\n\n{context[:1000]}", combined[:8]

        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=self.settings.openai_api_key)
        prompt = f"""You answer questions about a user's saved personal notes and facts.

Rules:
- Use ONLY the numbered items below when they are actually relevant to the question.
- If none of the items help answer the question, say you don't have enough information and set relevant_indices to [].
- Do not guess or invent details that are not supported by the items.
- Prefer precise, concise answers.

User question: {query}

Numbered memories and facts:
{context}

Respond with JSON only:
{{
  "answer": "your answer",
  "relevant_indices": [0, 2]
}}"""

        response = await client.chat.completions.create(
            model=self.settings.ai_model,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
        )
        raw = response.choices[0].message.content or "{}"
        try:
            parsed = json.loads(raw)
            answer = str(parsed.get("answer") or "No answer available.").strip()
            indices = parsed.get("relevant_indices") or []
            if not isinstance(indices, list):
                indices = []
        except json.JSONDecodeError:
            answer = raw.strip() or "No answer available."
            indices = []

        selected: list[SearchResult] = []
        for raw_idx in indices:
            try:
                idx = int(raw_idx)
            except (TypeError, ValueError):
                continue
            if 0 <= idx < len(indexed):
                selected.append(indexed[idx])

        if not selected:
            insufficient = any(
                phrase in answer.lower()
                for phrase in (
                    "don't have enough",
                    "do not have enough",
                    "couldn't find",
                    "could not find",
                    "no relevant",
                    "nothing relevant",
                    "not enough information",
                )
            )
            if insufficient or not indices:
                return answer, []

        if not selected and indexed:
            selected = indexed[: min(3, len(indexed))]

        return answer, selected[:8]
