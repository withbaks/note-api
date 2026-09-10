"""Schema-driven sync entity registry (server).

Understandings are AI/server emit-only — clients never push understanding mutations.
They are serialized by ServerSyncEmitter but have no client→server applier.

Threads / memory_threads are primarily server-emitted; client may push memory_thread
joins when confirming a mid-confidence attach.
"""

from __future__ import annotations

from typing import Any, Callable

# Appliers for client→server push
SYNC_ENTITY_REGISTRY: dict[str, dict[str, Any]] = {
    "memory_object": {"serializer": "_serialize_memory", "applier": "_apply_memory_mutation"},
    "helper": {"serializer": "_serialize_helper", "applier": "_apply_helper_mutation"},
    "memory_fact": {"serializer": "_serialize_fact", "applier": "_apply_fact_mutation"},
    "category": {"serializer": "_serialize_category", "applier": "_apply_category_mutation"},
    "memory_category": {
        "serializer": "_serialize_memory_category",
        "applier": "_apply_memory_category_mutation",
    },
    "person": {"serializer": "_serialize_person", "applier": "_apply_person_mutation"},
    "thread": {"serializer": "_serialize_thread", "applier": "_apply_thread_mutation"},
    "memory_thread": {
        "serializer": "_serialize_memory_thread",
        "applier": "_apply_memory_thread_mutation",
    },
}

# Emit-only entities (SYSTEM_DEVICE → client pull); not accepted on push
EMIT_ONLY_ENTITIES = frozenset({"understanding"})


def get_sync_applier(sync_service, entity_type: str) -> Callable | None:
    if entity_type in EMIT_ONLY_ENTITIES:
        return None
    entry = SYNC_ENTITY_REGISTRY.get(entity_type)
    if not entry:
        return None
    return getattr(sync_service, entry["applier"], None)


def get_sync_serializer(sync_service, entity_type: str) -> Callable | None:
    if entity_type == "understanding":
        return getattr(sync_service, "_serialize_understanding", None)
    entry = SYNC_ENTITY_REGISTRY.get(entity_type)
    if not entry:
        return None
    return getattr(sync_service, entry["serializer"], None)
