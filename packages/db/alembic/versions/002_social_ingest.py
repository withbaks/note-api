"""Social, ingest, and notification schema expansions."""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "002_social_ingest"
down_revision: Union[str, None] = "001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("memory_objects", sa.Column("media_uri", sa.String(1024), nullable=True))
    op.add_column("memory_objects", sa.Column("media_type", sa.String(64), nullable=True))

    op.add_column(
        "conversations",
        sa.Column("kind", sa.String(32), server_default="dm", nullable=False),
    )
    op.add_column("conversations", sa.Column("title", sa.String(256), nullable=True))
    op.add_column(
        "conversations",
        sa.Column("group_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("groups.id"), nullable=True),
    )
    op.create_index("ix_conversations_group_id", "conversations", ["group_id"])

    op.alter_column("shares", "shared_with_id", existing_type=postgresql.UUID(as_uuid=True), nullable=True)
    op.add_column(
        "shares",
        sa.Column(
            "shared_with_group_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("groups.id"),
            nullable=True,
        ),
    )
    op.add_column(
        "shares",
        sa.Column(
            "conversation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("conversations.id"),
            nullable=True,
        ),
    )
    op.execute(
        """
        UPDATE shares
        SET permissions = '{"view": true, "react": true, "forward": true, "ask_ai": true}'::jsonb
        WHERE permissions IS NULL
        """
    )
    op.alter_column(
        "shares",
        "permissions",
        existing_type=postgresql.JSONB(),
        nullable=False,
        server_default=sa.text("'{\"view\": true, \"react\": true, \"forward\": true, \"ask_ai\": true}'::jsonb"),
    )
    op.create_index("ix_shares_memory_object_id", "shares", ["memory_object_id"])
    op.create_index("ix_shares_shared_with_id", "shares", ["shared_with_id"])
    op.create_index("ix_shares_shared_with_group_id", "shares", ["shared_with_group_id"])
    op.create_index("ix_shares_conversation_id", "shares", ["conversation_id"])

    op.create_index("ix_reactions_memory_object_id", "reactions", ["memory_object_id"])
    op.create_index("ix_reactions_user_id", "reactions", ["user_id"])
    op.create_unique_constraint(
        "uq_reactions_memory_user_emoji",
        "reactions",
        ["memory_object_id", "user_id", "emoji"],
    )

    op.add_column(
        "ingest_items",
        sa.Column(
            "memory_object_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("memory_objects.id"),
            nullable=True,
        ),
    )
    op.add_column(
        "ingest_items",
        sa.Column("status", sa.String(32), server_default="pending", nullable=False),
    )
    op.add_column("ingest_items", sa.Column("title", sa.String(512), nullable=True))
    op.add_column("ingest_items", sa.Column("content_text", sa.Text(), nullable=True))
    op.add_column("ingest_items", sa.Column("media_type", sa.String(64), nullable=True))
    op.add_column("ingest_items", sa.Column("raw_payload", postgresql.JSONB(), nullable=True))
    op.create_index("ix_ingest_items_user_id", "ingest_items", ["user_id"])
    op.create_index("ix_ingest_items_memory_object_id", "ingest_items", ["memory_object_id"])

    op.create_index("ix_friendships_user_id", "friendships", ["user_id"])
    op.create_index("ix_friendships_friend_id", "friendships", ["friend_id"])
    op.create_unique_constraint("uq_friendships_user_friend", "friendships", ["user_id", "friend_id"])
    op.create_index("ix_groups_owner_id", "groups", ["owner_id"])

    op.create_table(
        "group_members",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("group_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("groups.id"), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("role", sa.String(32), server_default="member", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("group_id", "user_id", name="uq_group_members_group_user"),
    )
    op.create_index("ix_group_members_group_id", "group_members", ["group_id"])
    op.create_index("ix_group_members_user_id", "group_members", ["user_id"])

    op.create_table(
        "conversation_participants",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "conversation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("conversations.id"),
            nullable=False,
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("conversation_id", "user_id", name="uq_conversation_participants_pair"),
    )
    op.create_index(
        "ix_conversation_participants_conversation_id",
        "conversation_participants",
        ["conversation_id"],
    )
    op.create_index("ix_conversation_participants_user_id", "conversation_participants", ["user_id"])

    op.create_table(
        "push_tokens",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("device_id", sa.String(128), nullable=False),
        sa.Column("token", sa.String(512), nullable=False),
        sa.Column("platform", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "device_id", name="uq_push_tokens_user_device"),
    )
    op.create_index("ix_push_tokens_user_id", "push_tokens", ["user_id"])


def downgrade() -> None:
    op.drop_table("push_tokens")
    op.drop_table("conversation_participants")
    op.drop_table("group_members")

    op.drop_constraint("uq_friendships_user_friend", "friendships", type_="unique")
    op.drop_index("ix_friendships_friend_id", table_name="friendships")
    op.drop_index("ix_friendships_user_id", table_name="friendships")
    op.drop_index("ix_groups_owner_id", table_name="groups")

    op.drop_index("ix_ingest_items_memory_object_id", table_name="ingest_items")
    op.drop_index("ix_ingest_items_user_id", table_name="ingest_items")
    op.drop_column("ingest_items", "raw_payload")
    op.drop_column("ingest_items", "media_type")
    op.drop_column("ingest_items", "content_text")
    op.drop_column("ingest_items", "title")
    op.drop_column("ingest_items", "status")
    op.drop_column("ingest_items", "memory_object_id")

    op.drop_constraint("uq_reactions_memory_user_emoji", "reactions", type_="unique")
    op.drop_index("ix_reactions_user_id", table_name="reactions")
    op.drop_index("ix_reactions_memory_object_id", table_name="reactions")

    op.drop_index("ix_shares_conversation_id", table_name="shares")
    op.drop_index("ix_shares_shared_with_group_id", table_name="shares")
    op.drop_index("ix_shares_shared_with_id", table_name="shares")
    op.drop_index("ix_shares_memory_object_id", table_name="shares")
    op.drop_column("shares", "conversation_id")
    op.drop_column("shares", "shared_with_group_id")
    op.alter_column(
        "shares",
        "permissions",
        existing_type=postgresql.JSONB(),
        nullable=True,
        server_default=None,
    )
    op.alter_column("shares", "shared_with_id", existing_type=postgresql.UUID(as_uuid=True), nullable=False)

    op.drop_index("ix_conversations_group_id", table_name="conversations")
    op.drop_column("conversations", "group_id")
    op.drop_column("conversations", "title")
    op.drop_column("conversations", "kind")

    op.drop_column("memory_objects", "media_type")
    op.drop_column("memory_objects", "media_uri")
