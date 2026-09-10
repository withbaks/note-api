from note_search.topic_clusters import (
    TopicClusterNode,
    TopicRecord,
    build_topic_clusters_from_records,
)


def test_single_topic_cluster():
    records = [TopicRecord(key="travel", label="Travel", memory_ids={"m1", "m2"})]
    result = build_topic_clusters_from_records(records)
    assert len(result.nodes) == 1
    assert result.nodes[0].key == "travel"
    assert result.nodes[0].count == 2


def test_heuristic_groups_related_topics():
    records = [
        TopicRecord(key="travel", label="Travel", memory_ids={"m1", "m2", "m3"}),
        TopicRecord(key="japan trip", label="Japan trip", memory_ids={"m1", "m2"}),
        TopicRecord(key="family", label="Family", memory_ids={"m4"}),
    ]
    result = build_topic_clusters_from_records(records)
    travel_parent = next(n for n in result.nodes if n.key == "travel" and n.parent_key is None)
    assert "japan trip" in travel_parent.member_keys
    japan_child = next((n for n in result.nodes if n.key == "japan trip"), None)
    assert japan_child is not None
    assert japan_child.parent_key == "travel"


def test_build_nodes_counts_union_for_parent():
    records = [
        TopicRecord(key="travel", label="Travel", memory_ids={"m1", "m2"}),
        TopicRecord(key="flights", label="Flights", memory_ids={"m2", "m3"}),
    ]
    groups = {
        "travel": records,
    }
    from note_search.topic_clusters import _build_nodes

    nodes = _build_nodes(groups)
    parent = next(n for n in nodes if n.parent_key is None)
    assert parent.count == 3
