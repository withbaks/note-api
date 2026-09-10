"""Post-retrieval boosts using must_include, temporal, and people metadata."""

from __future__ import annotations

import re
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from note_db.models import Understanding
from note_search.service import SearchResult

_MONTH_TOKENS = (
    "january",
    "february",
    "march",
    "april",
    "may",
    "june",
    "july",
    "august",
    "september",
    "october",
    "november",
    "december",
    "jan",
    "feb",
    "mar",
    "apr",
    "jun",
    "jul",
    "aug",
    "sep",
    "sept",
    "oct",
    "nov",
    "dec",
)

_RELATIONSHIP_WORDS = frozenset(
    {
        "mum",
        "mom",
        "mother",
        "dad",
        "father",
        "papa",
        "mama",
        "sister",
        "brother",
        "wife",
        "husband",
        "partner",
        "friend",
    }
)


_STOPWORDS = frozenset(
    {
        "what",
        "when",
        "where",
        "which",
        "who",
        "whom",
        "whose",
        "why",
        "how",
        "the",
        "this",
        "that",
        "these",
        "those",
        "with",
        "from",
        "about",
        "should",
        "would",
        "could",
        "have",
        "need",
        "want",
        "tell",
        "show",
        "find",
        "give",
        "get",
        "did",
        "does",
        "was",
        "were",
        "been",
        "being",
        "into",
        "over",
        "under",
        "after",
        "before",
        "during",
        "while",
        "than",
        "then",
        "them",
        "they",
        "their",
        "there",
        "here",
        "your",
        "you",
        "our",
        "for",
        "her",
        "his",
        "and",
        "but",
        "not",
        "any",
        "all",
        "some",
        "more",
        "most",
        "just",
        "also",
        "only",
        "like",
        "idea",
        "plan",
        "note",
        "notes",
        "memory",
        "remind",
        "reminder",
        "september",
        "october",
        "november",
        "december",
        "january",
        "february",
        "march",
        "april",
        "june",
        "july",
        "august",
    }
)


def extract_must_include_heuristic(query: str) -> list[str]:
    """Pull likely person/entity tokens from the query without an LLM."""
    tokens: list[str] = []
    for match in re.finditer(r"\b([A-Z][a-z]{1,24})\b", query):
        word = match.group(1)
        if word.lower() in _STOPWORDS:
            continue
        tokens.append(word)
    lower = query.lower()
    for word in _RELATIONSHIP_WORDS:
        if re.search(rf"\b{re.escape(word)}\b", lower):
            tokens.append(word)
    # Dedupe case-insensitively, preserve order
    seen: set[str] = set()
    out: list[str] = []
    for t in tokens:
        key = t.lower()
        if key in seen or key in _STOPWORDS:
            continue
        seen.add(key)
        out.append(t)
    return out[:8]


def extract_temporal_heuristic(query: str) -> str | None:
    lower = query.lower()
    for month in _MONTH_TOKENS:
        if re.search(rf"\b{re.escape(month)}\b", lower):
            return month
    if re.search(r"\b(\d{1,2})(st|nd|rd|th)?\s+month\b", lower):
        return lower
    if re.search(r"\b(tomorrow|today|next week|birthday)\b", lower):
        m = re.search(r"\b(tomorrow|today|next week|birthday)\b", lower)
        return m.group(1) if m else None
    return None


def _preview_matches_token(preview: str, token: str) -> bool:
    return bool(re.search(rf"\b{re.escape(token)}\b", preview, flags=re.IGNORECASE))


async def apply_retrieval_boosts(
    session: AsyncSession,
    results: list[SearchResult],
    *,
    must_include: list[str] | None,
    temporal_filter: str | None,
) -> list[SearchResult]:
    """Boost candidates matching must_include names or temporal hints."""
    if not results:
        return results

    tokens = [t.strip() for t in (must_include or []) if t and t.strip()]
    temporal = (temporal_filter or "").strip().lower() or None

    memory_ids = [r.memory_id for r in results if r.memory_id]
    people_by_memory: dict[UUID, list[str]] = {}
    temporal_by_memory: dict[UUID, list[str]] = {}
    if memory_ids:
        understandings = (
            await session.scalars(
                select(Understanding).where(
                    Understanding.memory_object_id.in_(memory_ids),
                    Understanding.superseded_by.is_(None),
                )
            )
        ).all()
        for u in understandings:
            raw = u.raw if isinstance(u.raw, dict) else {}
            refs = [str(x) for x in (raw.get("people_refs") or []) if x]
            if refs:
                people_by_memory[u.memory_object_id] = refs
            trefs = [str(x).lower() for x in (raw.get("temporal_refs") or []) if x]
            if trefs:
                temporal_by_memory[u.memory_object_id] = trefs

    boosted: list[SearchResult] = []
    for result in results:
        score = result.score
        preview = result.content_preview or ""

        if tokens:
            hit_count = 0
            for token in tokens:
                if _preview_matches_token(preview, token):
                    hit_count += 1
                    continue
                if result.memory_id and result.memory_id in people_by_memory:
                    refs = people_by_memory[result.memory_id]
                    if any(token.lower() in ref.lower() or ref.lower() in token.lower() for ref in refs):
                        hit_count += 1
            if hit_count:
                score += 0.12 * hit_count
            elif tokens and result.memory_id:
                # Soft demote when required people tokens never appear
                score *= 0.85

        if temporal:
            t_hit = temporal in preview.lower()
            if not t_hit and result.memory_id and result.memory_id in temporal_by_memory:
                t_hit = any(temporal in ref or ref in temporal for ref in temporal_by_memory[result.memory_id])
            if t_hit:
                score += 0.1

        if score != result.score:
            boosted.append(result.model_copy(update={"score": score}))
        else:
            boosted.append(result)

    return sorted(boosted, key=lambda r: r.score, reverse=True)
