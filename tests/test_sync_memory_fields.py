"""Guard: server memory serialization stays aligned with client pull fields."""

from note_sync.service import SYNCABLE_FIELDS

# Must match note/src/lib/syncRegistry.ts MEMORY_OBJECT_PULL_FIELDS
CLIENT_MEMORY_PULL_FIELDS = {
    "type",
    "origin",
    "content_text",
    "structured_title",
    "structured_value",
    "media_uri",
    "media_type",
    "link_metadata",
    "visibility",
    "lifecycle",
    "favorite",
    "ai_state",
    "device_id",
    "version",
    "deleted_at",
    "hlc",
}

SERIALIZE_MEMORY_KEYS = {
    "id",
    "type",
    "origin",
    "content_text",
    "structured_title",
    "structured_value",
    "media_uri",
    "media_type",
    "link_metadata",
    "visibility",
    "lifecycle",
    "favorite",
    "ai_state",
    "device_id",
    "hlc",
    "version",
    "deleted_at",
    "created_at",
    "updated_at",
}


def test_serialize_memory_covers_client_pull_fields():
    assert CLIENT_MEMORY_PULL_FIELDS.issubset(SERIALIZE_MEMORY_KEYS)


def test_syncable_memory_fields_subset_of_client_pull():
    for field in SYNCABLE_FIELDS["memory_object"]:
        assert field in CLIENT_MEMORY_PULL_FIELDS, f"missing client pull field: {field}"


def test_understanding_is_emit_only():
    from note_sync.registry import EMIT_ONLY_ENTITIES, get_sync_applier

    assert "understanding" in EMIT_ONLY_ENTITIES
    assert get_sync_applier(object(), "understanding") is None
