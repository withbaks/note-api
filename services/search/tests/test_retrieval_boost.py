"""Tests for hybrid retrieval boosts and heuristics."""

from note_search.query_router import QueryClass, classify_query
from note_search.retrieval_boost import (
    extract_must_include_heuristic,
    extract_temporal_heuristic,
)
from note_search.service import SearchResult
from note_search.rrf import reciprocal_rank_fusion
from uuid import uuid4


def test_extract_must_include_person_name():
    tokens = extract_must_include_heuristic("What did Tobi suggest about distribution?")
    assert any(t.lower() == "tobi" for t in tokens)


def test_extract_must_include_relationship():
    tokens = extract_must_include_heuristic("What should I get Mum for her birthday?")
    assert any(t.lower() == "mum" for t in tokens)


def test_extract_temporal_month():
    assert extract_temporal_heuristic("remember in September") == "september"
    assert extract_temporal_heuristic("get gift before birthday") == "birthday"


def test_keyword_class_still_keyword():
    assert classify_query("Nike shoes") == QueryClass.keyword


def test_relation_channel_survives_rrf_with_direct_hits():
    mem_a = uuid4()
    mem_b = uuid4()
    direct = [
        SearchResult(memory_id=mem_a, content_preview="birthday Sept 14", match_type="fts", score=0.7),
    ]
    relation = [
        SearchResult(
            memory_id=mem_b,
            content_preview="Saw a nice handbag",
            match_type="relation_expand",
            score=0.42,
        ),
        SearchResult(
            memory_id=mem_a,
            content_preview="Mum birthday",
            match_type="relation_expand",
            score=0.42,
        ),
    ]
    merged = reciprocal_rank_fusion(
        [direct, relation],
        key_fn=lambda r: f"memory:{r.memory_id}",
    )
    ids = {r.memory_id for r in merged}
    assert mem_a in ids
    assert mem_b in ids
