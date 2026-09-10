"""Adaptive query routing for smart search."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import Enum

from note_core.config import get_settings
from note_search.query_understanding import looks_like_question


class QueryClass(str, Enum):
    keyword = "keyword"
    semantic = "semantic"
    complex = "complex"


@dataclass
class RoutedQuery:
    query_class: QueryClass
    search_queries: list[str]
    temporal_filter: str | None = None
    must_include: list[str] | None = None


_COMPLEX_SIGNALS = (
    r"\band\b",
    r"\bor\b",
    r"\bcompare\b",
    r"\ball\b",
    r"\bevery\b",
    r"\bmost\b",
    r"\bleast\b",
    r"\bwhich\b.*\band\b",
    r"\bhow many\b",
)


def classify_query(query: str) -> QueryClass:
    q = query.strip()
    if not q:
        return QueryClass.keyword

    lower = q.lower()
    if any(re.search(pattern, lower) for pattern in _COMPLEX_SIGNALS) and len(q.split()) >= 8:
        return QueryClass.complex

    if looks_like_question(q):
        return QueryClass.semantic

    words = [w for w in re.findall(r"[a-z0-9]+", lower) if len(w) > 1]
    if len(words) <= 3 and not q.endswith("?"):
        return QueryClass.keyword

    return QueryClass.semantic


async def route_query(query: str) -> RoutedQuery:
    from note_search.retrieval_boost import extract_must_include_heuristic, extract_temporal_heuristic

    query_class = classify_query(query)
    heuristic_must = extract_must_include_heuristic(query)
    heuristic_temporal = extract_temporal_heuristic(query)

    if query_class == QueryClass.keyword:
        return RoutedQuery(
            query_class=query_class,
            search_queries=[query.strip()],
            temporal_filter=heuristic_temporal,
            must_include=heuristic_must,
        )

    settings = get_settings()
    if not settings.openai_api_key:
        return RoutedQuery(
            query_class=query_class,
            search_queries=[query.strip()],
            temporal_filter=heuristic_temporal,
            must_include=heuristic_must,
        )

    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=settings.openai_api_key)
    mode = "decompose" if query_class == QueryClass.complex else "rewrite"
    prompt = f"""You help search a user's personal notes. Mode: {mode}.

User query: {query}

Return JSON only:
{{
  "search_queries": ["query1", "query2"],
  "temporal_filter": null,
  "must_include": []
}}

Rules:
- search_queries: 1-4 short search phrases that would find relevant saved notes.
- For vague temporal queries, include month names and ordinals (e.g. "7th month" -> "July", "seventh month").
- must_include: important entities/names from the query, or [].
- temporal_filter: optional free-text time hint, or null.
"""

    try:
        response = await client.chat.completions.create(
            model=settings.ai_model,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            max_tokens=300,
        )
        raw = response.choices[0].message.content or "{}"
        parsed = json.loads(raw)
        search_queries = parsed.get("search_queries") or [query.strip()]
        if not isinstance(search_queries, list):
            search_queries = [query.strip()]
        cleaned = [str(item).strip() for item in search_queries if str(item).strip()]
        if query.strip() not in cleaned:
            cleaned.insert(0, query.strip())
        llm_must = parsed.get("must_include") or []
        if not isinstance(llm_must, list):
            llm_must = []
        must_include = list(
            dict.fromkeys([*(str(x).strip() for x in llm_must if str(x).strip()), *heuristic_must])
        )
        temporal = parsed.get("temporal_filter") or heuristic_temporal
        return RoutedQuery(
            query_class=query_class,
            search_queries=cleaned[:4],
            temporal_filter=temporal,
            must_include=must_include,
        )
    except Exception:
        return RoutedQuery(
            query_class=query_class,
            search_queries=[query.strip()],
            temporal_filter=heuristic_temporal,
            must_include=heuristic_must,
        )
