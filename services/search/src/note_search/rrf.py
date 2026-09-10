"""Reciprocal rank fusion for multi-channel search results."""

from __future__ import annotations

from collections import defaultdict
from typing import TypeVar

T = TypeVar("T")

RRF_K = 60


def reciprocal_rank_fusion(
    ranked_lists: list[list[T]],
    *,
    key_fn,
    score_attr: str = "score",
) -> list[T]:
    """Merge multiple ranked result lists using RRF."""
    if not ranked_lists:
        return []

    fused_scores: dict[str, float] = defaultdict(float)
    best_item: dict[str, T] = {}

    for results in ranked_lists:
        for rank, item in enumerate(results, start=1):
            key = key_fn(item)
            fused_scores[key] += 1.0 / (RRF_K + rank)
            if key not in best_item:
                best_item[key] = item

    ordered_keys = sorted(fused_scores.keys(), key=lambda k: fused_scores[k], reverse=True)
    merged: list[T] = []
    for key in ordered_keys:
        item = best_item[key]
        score = fused_scores[key]
        if hasattr(item, "model_copy"):
            merged.append(item.model_copy(update={score_attr: score}))
        else:
            setattr(item, score_attr, score)
            merged.append(item)
    return merged
