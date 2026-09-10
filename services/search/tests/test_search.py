from uuid import uuid4

from note_search.query_router import QueryClass, classify_query
from note_search.structured_query import QueryIntent, classify_intent, _extract_month
from note_search.query_understanding import expand_search_queries, looks_like_question
from note_search.rrf import reciprocal_rank_fusion
from note_search.service import SearchResult


def test_classify_keyword_query():
    assert classify_query("Nike shoes") == QueryClass.keyword


def test_classify_semantic_question():
    assert classify_query("what do i need to remember on the 7th month") == QueryClass.semantic


def test_classify_complex_query():
    q = (
        "What were all the things I saved about places I wanted to visit with Joshua, "
        "and which ones did I seem most interested in?"
    )
    assert classify_query(q) == QueryClass.complex


def test_rrf_prefers_items_in_multiple_lists():
    a = SearchResult(memory_id=uuid4(), content_preview="a", match_type="fts", score=0.5)
    b = SearchResult(memory_id=uuid4(), content_preview="b", match_type="fts", score=0.5)
    c = SearchResult(memory_id=uuid4(), content_preview="c", match_type="fts", score=0.5)

    merged = reciprocal_rank_fusion([[a, b], [b, c]], key_fn=lambda r: r.content_preview)
    assert merged[0].content_preview == "b"


GOLDEN_QUERY_CASES = [
    {
        "query": "what do i need to remember on the 7th month",
        "expects_question": True,
        "expansions_contain": ["july", "7th month"],
    },
    {
        "query": "Nike shoes",
        "expects_question": False,
        "expansions_contain": [],
    },
    {
        "query": "things about Joshua trips",
        "expects_question": False,
        "expansions_contain": [],
    },
]


def test_golden_query_expectations():
    for case in GOLDEN_QUERY_CASES:
        query = case["query"]
        assert looks_like_question(query) is case["expects_question"]
        expanded = [item.lower() for item in expand_search_queries(query)]
        for token in case["expansions_contain"]:
            assert any(token in item for item in expanded), f"{token} missing for {query}"


def test_classify_intent_friends_count():
    assert classify_intent("how many friends do I have?") == QueryIntent.aggregate


def test_classify_intent_social_friends():
    assert classify_intent("who are my friends") == QueryIntent.social


def test_classify_intent_temporal_month():
    assert classify_intent("what do i need to remember on the 7th month") == QueryIntent.temporal
    assert _extract_month("7th month") == 7


def test_classify_intent_inventory():
    assert classify_intent("everything about dad") == QueryIntent.inventory


def test_classify_intent_memory_default():
    assert classify_intent("trip to Lagos") == QueryIntent.memory_recall
