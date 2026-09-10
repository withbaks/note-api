from __future__ import annotations

import math
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from note_core.config import get_settings
from note_db.models import Lifecycle, MemoryObject, Understanding


class TopicClusterNode(BaseModel):
    key: str
    label: str
    count: int
    parent_key: str | None = None
    member_keys: list[str] = Field(default_factory=list)


class TopicClusterResponse(BaseModel):
    nodes: list[TopicClusterNode]
    source: str = "heuristic"


@dataclass
class TopicRecord:
    key: str
    label: str
    memory_ids: set[str] = field(default_factory=set)
    is_topic: bool = True
    score: float = 0.0


def _recency_weight(created_at: datetime) -> float:
    now = datetime.now(timezone.utc)
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    age_days = (now - created_at).total_seconds() / 86_400
    if age_days <= 1:
        return 3.0
    if age_days <= 7:
        return 2.0
    if age_days <= 14:
        return 1.5
    if age_days <= 30:
        return 0.6
    return 0.15


def _normalize_key(label: str) -> str:
    cleaned = re.sub(r"\s+", " ", label.strip().lower())
    return cleaned


def _title_label(label: str) -> str:
    text = re.sub(r"\s+", " ", label.strip())
    if not text:
        return text
    return text[0].upper() + text[1:]


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _word_tokens(label: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", label.lower()) if len(w) > 2}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def _co_occurrence(records: list[TopicRecord]) -> dict[tuple[str, str], int]:
    pair_counts: dict[tuple[str, str], int] = defaultdict(int)
    memory_to_keys: dict[str, list[str]] = defaultdict(list)
    for record in records:
        for memory_id in record.memory_ids:
            memory_to_keys[memory_id].append(record.key)
    for keys in memory_to_keys.values():
        unique = sorted(set(keys))
        for i, a in enumerate(unique):
            for b in unique[i + 1 :]:
                pair_counts[(a, b)] += 1
    return pair_counts


def _similarity(a: TopicRecord, b: TopicRecord, co_counts: dict[tuple[str, str], int]) -> float:
    pair = tuple(sorted((a.key, b.key)))
    co = co_counts.get(pair, 0)
    co_score = min(1.0, co / 2.0)

    word_score = _jaccard(_word_tokens(a.label), _word_tokens(b.label))
    subset_score = 0.0
    if a.key in b.key or b.key in a.key:
        subset_score = 0.75
    elif _word_tokens(a.label) & _word_tokens(b.label):
        subset_score = 0.45

    return max(co_score, word_score, subset_score)


class UnionFind:
    def __init__(self, keys: list[str]) -> None:
        self.parent = {k: k for k in keys}

    def find(self, key: str) -> str:
        while self.parent[key] != key:
            self.parent[key] = self.parent[self.parent[key]]
            key = self.parent[key]
        return key

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def _cluster_with_embeddings(
    records: list[TopicRecord],
    embeddings: dict[str, list[float]],
    *,
    merge_threshold: float = 0.78,
) -> dict[str, list[TopicRecord]]:
    co_counts = _co_occurrence(records)
    uf = UnionFind([r.key for r in records])
    by_key = {r.key: r for r in records}

    for i, a in enumerate(records):
        for b in records[i + 1 :]:
            emb_a = embeddings.get(a.key)
            emb_b = embeddings.get(b.key)
            emb_score = _cosine(emb_a, emb_b) if emb_a and emb_b else 0.0
            score = max(emb_score, _similarity(a, b, co_counts))
            if score >= merge_threshold:
                uf.union(a.key, b.key)

    groups: dict[str, list[TopicRecord]] = defaultdict(list)
    for record in records:
        groups[uf.find(record.key)].append(record)
    return groups


def _cluster_heuristic(
    records: list[TopicRecord],
    *,
    merge_threshold: float = 0.42,
) -> dict[str, list[TopicRecord]]:
    co_counts = _co_occurrence(records)
    uf = UnionFind([r.key for r in records])

    for i, a in enumerate(records):
        for b in records[i + 1 :]:
            if _similarity(a, b, co_counts) >= merge_threshold:
                uf.union(a.key, b.key)

    groups: dict[str, list[TopicRecord]] = defaultdict(list)
    for record in records:
        groups[uf.find(record.key)].append(record)
    return groups


def _pick_parent(members: list[TopicRecord]) -> TopicRecord:
    return sorted(
        members,
        key=lambda m: (-len(m.memory_ids), len(m.label.split()), len(m.label)),
    )[0]


def _build_nodes(groups: dict[str, list[TopicRecord]]) -> list[TopicClusterNode]:
    nodes: list[TopicClusterNode] = []
    records_by_key: dict[str, TopicRecord] = {}

    for members in groups.values():
        for member in members:
            records_by_key[member.key] = member
        members = sorted(members, key=lambda m: m.label.lower())
        if len(members) == 1:
            only = members[0]
            nodes.append(
                TopicClusterNode(
                    key=only.key,
                    label=only.label,
                    count=len(only.memory_ids),
                    parent_key=None,
                    member_keys=[only.key],
                )
            )
            continue

        parent = _pick_parent(members)
        member_keys = sorted({m.key for m in members})
        union_memories: set[str] = set()
        for m in members:
            union_memories |= m.memory_ids

        nodes.append(
            TopicClusterNode(
                key=parent.key,
                label=parent.label,
                count=len(union_memories),
                parent_key=None,
                member_keys=member_keys,
            )
        )

        for child in members:
            if child.key == parent.key:
                continue
            nodes.append(
                TopicClusterNode(
                    key=child.key,
                    label=child.label,
                    count=len(child.memory_ids),
                    parent_key=parent.key,
                    member_keys=[child.key],
                )
            )

    nodes.sort(key=lambda n: (-_node_trend_score(n, records_by_key), -n.count, n.label.lower()))
    return nodes


def _node_trend_score(node: TopicClusterNode, records_by_key: dict[str, TopicRecord]) -> float:
    scores = [records_by_key[k].score for k in node.member_keys if k in records_by_key]
    if node.key in records_by_key:
        scores.append(records_by_key[node.key].score)
    return max(scores) if scores else 0.0


def _collect_records(
    rows: list[tuple[Understanding, MemoryObject]],
) -> list[TopicRecord]:
    by_key: dict[str, TopicRecord] = {}

    for understanding, memory in rows:
        memory_id = str(understanding.memory_object_id)
        weight = _recency_weight(memory.created_at)
        topic_items = [(t, True) for t in (understanding.topics or []) if isinstance(t, str)]
        tag_items = [(t, False) for t in (understanding.tags or []) if isinstance(t, str)]

        for raw, is_topic in [*topic_items, *tag_items]:
            label = raw.strip()
            if not label:
                continue
            key = _normalize_key(label)
            if not key:
                continue
            existing = by_key.get(key)
            if not existing:
                by_key[key] = TopicRecord(key=key, label=_title_label(label), is_topic=is_topic)
            existing = by_key[key]
            existing.memory_ids.add(memory_id)
            existing.score += weight
            if is_topic and not existing.is_topic:
                existing.is_topic = True
                existing.label = _title_label(label)

    return sorted(
        [r for r in by_key.values() if r.score >= 0.4],
        key=lambda r: (-r.score, r.label.lower()),
    )


async def _embed_labels(records: list[TopicRecord]) -> dict[str, list[float]] | None:
    settings = get_settings()
    if not settings.openai_api_key or len(records) < 2:
        return None

    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=settings.openai_api_key)
    response = await client.embeddings.create(
        model="text-embedding-3-small",
        input=[r.label for r in records],
    )
    return {
        record.key: item.embedding
        for record, item in zip(records, response.data, strict=True)
    }


def build_topic_clusters_from_records(
    records: list[TopicRecord],
    embeddings: dict[str, list[float]] | None = None,
) -> TopicClusterResponse:
    if not records:
        return TopicClusterResponse(nodes=[], source="heuristic")
    if len(records) == 1:
        only = records[0]
        return TopicClusterResponse(
            nodes=[
                TopicClusterNode(
                    key=only.key,
                    label=only.label,
                    count=len(only.memory_ids),
                    parent_key=None,
                    member_keys=[only.key],
                )
            ],
            source="heuristic",
        )

    if embeddings:
        groups = _cluster_with_embeddings(records, embeddings)
        source = "embedding"
    else:
        groups = _cluster_heuristic(records)
        source = "heuristic"

    return TopicClusterResponse(nodes=_build_nodes(groups), source=source)


class TopicClusterService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_clusters(self, user_id: UUID) -> TopicClusterResponse:
        stmt = (
            select(Understanding, MemoryObject)
            .join(MemoryObject, Understanding.memory_object_id == MemoryObject.id)
            .where(
                MemoryObject.user_id == user_id,
                MemoryObject.lifecycle == Lifecycle.active,
                Understanding.superseded_by.is_(None),
            )
        )
        rows = (await self.session.execute(stmt)).all()
        records = _collect_records(list(rows))
        embeddings = await _embed_labels(records)
        return build_topic_clusters_from_records(records, embeddings)
