"""Initial schema."""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision: str = "001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

memorytype_enum = postgresql.ENUM("text", "structured", name="memorytype", create_type=False)
visibility_enum = postgresql.ENUM("private", "shared", "group", "public", name="visibility", create_type=False)
lifecycle_enum = postgresql.ENUM("active", "hidden", "archived", "deleted", name="lifecycle", create_type=False)
aistate_enum = postgresql.ENUM(
    "captured", "processing", "understood", "organized", "indexed", "failed",
    name="aistate",
    create_type=False,
)
helpersource_enum = postgresql.ENUM("heuristic", "llm", "user", name="helpersource", create_type=False)
helperstatus_enum = postgresql.ENUM("suggested", "accepted", "dismissed", name="helperstatus", create_type=False)
categorysource_enum = postgresql.ENUM("user", "ai", name="categorysource", create_type=False)


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("email", sa.String(320), unique=True, nullable=True),
        sa.Column("password_hash", sa.String(255), nullable=True),
        sa.Column("apple_sub", sa.String(255), unique=True, nullable=True),
        sa.Column("username", sa.String(64), unique=True, nullable=True),
        sa.Column("display_name", sa.String(128), nullable=True),
        sa.Column("avatar_url", sa.String(512), nullable=True),
        sa.Column("ai_enabled", sa.Boolean(), server_default="true"),
        sa.Column("ai_use_notes_context", sa.Boolean(), server_default="true"),
        sa.Column("helper_auto_accept", sa.Boolean(), server_default="false"),
        sa.Column("notifications_enabled", sa.Boolean(), server_default="true"),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "devices",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("name", sa.String(128), server_default="Unknown Device"),
        sa.Column("platform", sa.String(64), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_devices_user_id", "devices", ["user_id"])

    op.create_table(
        "sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("device_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("devices.id"), nullable=False),
        sa.Column("refresh_token_hash", sa.String(255), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_sessions_user_id", "sessions", ["user_id"])
    op.create_index("ix_sessions_device_id", "sessions", ["device_id"])

    for enum_name, values in [
        ("memorytype", ("text", "structured")),
        ("visibility", ("private", "shared", "group", "public")),
        ("lifecycle", ("active", "hidden", "archived", "deleted")),
        ("aistate", ("captured", "processing", "understood", "organized", "indexed", "failed")),
        ("helpersource", ("heuristic", "llm", "user")),
        ("helperstatus", ("suggested", "accepted", "dismissed")),
        ("categorysource", ("user", "ai")),
    ]:
        sa.Enum(*values, name=enum_name).create(op.get_bind(), checkfirst=True)

    op.create_table(
        "memory_objects",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("type", memorytype_enum, server_default="text"),
        sa.Column("origin", sa.String(64), server_default="capture"),
        sa.Column("content_text", sa.Text(), nullable=True),
        sa.Column("structured_title", sa.String(256), nullable=True),
        sa.Column("structured_value", sa.Text(), nullable=True),
        sa.Column("visibility", visibility_enum, server_default="private"),
        sa.Column("lifecycle", lifecycle_enum, server_default="active"),
        sa.Column("favorite", sa.Boolean(), server_default="false"),
        sa.Column("ai_state", aistate_enum, server_default="captured"),
        sa.Column("device_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("hlc", sa.String(128), server_default="0:0:server"),
        sa.Column("version", sa.Integer(), server_default="1"),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_memory_objects_user_id", "memory_objects", ["user_id"])
    op.create_index("ix_memory_objects_user_lifecycle", "memory_objects", ["user_id", "lifecycle"])
    op.create_index("ix_memory_objects_user_updated", "memory_objects", ["user_id", "updated_at"])

    op.create_table(
        "memory_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("memory_object_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("memory_objects.id"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("hlc", sa.String(128), nullable=False),
        sa.Column("device_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_memory_versions_memory_object_id", "memory_versions", ["memory_object_id"])

    op.create_table(
        "understandings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("memory_object_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("memory_objects.id"), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("tags", postgresql.JSONB(), nullable=True),
        sa.Column("topics", postgresql.JSONB(), nullable=True),
        sa.Column("connections", postgresql.JSONB(), nullable=True),
        sa.Column("why_it_matters", sa.Text(), nullable=True),
        sa.Column("keyword_summary", sa.Text(), nullable=True),
        sa.Column("raw", postgresql.JSONB(), nullable=True),
        sa.Column("superseded_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_understandings_memory_object_id", "understandings", ["memory_object_id"])

    op.create_table(
        "helpers",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("memory_object_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("memory_objects.id"), nullable=False),
        sa.Column("key", sa.String(128), nullable=False),
        sa.Column("value", sa.Text(), nullable=True),
        sa.Column("person", sa.String(128), nullable=True),
        sa.Column("source", helpersource_enum, server_default="heuristic"),
        sa.Column("status", helperstatus_enum, server_default="suggested"),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("hlc", sa.String(128), server_default="0:0:server"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_helpers_memory_object_id", "helpers", ["memory_object_id"])
    op.create_index("ix_helpers_key", "helpers", ["key"])

    op.create_table(
        "memory_facts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("key", sa.String(256), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("person", sa.String(128), nullable=True),
        sa.Column("user_override", sa.Boolean(), server_default="false"),
        sa.Column("source_memory_ids", postgresql.JSONB(), nullable=True),
        sa.Column("hlc", sa.String(128), server_default="0:0:server"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "key", "person", name="uq_memory_facts_user_key_person"),
    )
    op.create_index("ix_memory_facts_user_id", "memory_facts", ["user_id"])

    op.create_table(
        "categories",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("color", sa.String(32), nullable=True),
        sa.Column("source", categorysource_enum, server_default="user"),
        sa.Column("hlc", sa.String(128), server_default="0:0:server"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "name", name="uq_categories_user_name"),
    )
    op.create_index("ix_categories_user_id", "categories", ["user_id"])

    op.create_table(
        "memory_categories",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("memory_object_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("memory_objects.id"), nullable=False),
        sa.Column("category_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("categories.id"), nullable=False),
        sa.Column("source", categorysource_enum, server_default="user"),
        sa.Column("hlc", sa.String(128), server_default="0:0:server"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("memory_object_id", "category_id", name="uq_memory_categories_pair"),
    )

    op.create_table(
        "ai_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("memory_object_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("memory_objects.id"), nullable=False),
        sa.Column("state", aistate_enum, server_default="captured"),
        sa.Column("attempts", sa.Integer(), server_default="0"),
        sa.Column("max_attempts", sa.Integer(), server_default="5"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_ai_jobs_memory_object_id", "ai_jobs", ["memory_object_id"])

    op.create_table(
        "entities",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("memory_object_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("memory_objects.id"), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("entity_type", sa.String(64), nullable=False),
        sa.Column("value", sa.String(512), nullable=False),
        sa.Column("embedding", Vector(1536), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_entities_user_id", "entities", ["user_id"])
    op.create_index("ix_entities_memory_object_id", "entities", ["memory_object_id"])

    op.create_table(
        "sync_mutations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("device_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("entity_type", sa.String(64), nullable=False),
        sa.Column("entity_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("operation", sa.String(32), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("hlc", sa.String(128), nullable=False),
        sa.Column("applied_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_sync_mutations_user_id", "sync_mutations", ["user_id"])

    op.create_table(
        "sync_cursors",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("device_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("cursor", sa.String(128), server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "device_id", name="uq_sync_cursors_user_device"),
    )

    # Social stubs
    op.create_table(
        "friendships",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("friend_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("status", sa.String(32), server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_table(
        "conversations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_table(
        "groups",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_table(
        "shares",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("memory_object_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("memory_objects.id"), nullable=False),
        sa.Column("shared_with_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("permissions", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_table(
        "reactions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("memory_object_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("memory_objects.id"), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("emoji", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_table(
        "ingest_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("source_type", sa.String(64), nullable=False),
        sa.Column("source_url", sa.String(1024), nullable=True),
        sa.Column("source_metadata", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    for table in [
        "ingest_items", "reactions", "shares", "groups", "conversations", "friendships",
        "sync_cursors", "sync_mutations", "entities", "ai_jobs", "memory_categories",
        "categories", "memory_facts", "helpers", "understandings", "memory_versions",
        "memory_objects", "sessions", "devices", "users",
    ]:
        op.drop_table(table)

    for enum_name in [
        "categorysource", "helperstatus", "helpersource", "aistate",
        "lifecycle", "visibility", "memorytype",
    ]:
        sa.Enum(name=enum_name).drop(op.get_bind(), checkfirst=True)

    op.execute("DROP EXTENSION IF EXISTS vector")
