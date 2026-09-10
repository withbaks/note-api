"""Threads, append-only fact fields, fact embeddings."""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision: str = "013_threads_fact_resolution"
down_revision: Union[str, None] = "012_pipeline_hardening"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "memory_facts",
        sa.Column("mention_count", sa.Integer(), server_default="1", nullable=False),
    )
    op.add_column(
        "memory_facts",
        sa.Column("last_reaffirmed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "memory_facts",
        sa.Column("expected_volatility", sa.String(32), server_default="slow", nullable=False),
    )
    op.add_column(
        "memory_facts",
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "memory_facts",
        sa.Column("embedding", Vector(1536), nullable=True),
    )

    op.create_table(
        "threads",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("status", sa.String(32), server_default="active", nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("primary_entities", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("first_memory_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_memory_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("embedding", Vector(1536), nullable=True),
        sa.Column("hlc", sa.String(128), server_default="0:0:server", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_threads_user_id", "threads", ["user_id"])
    op.create_index("ix_threads_status", "threads", ["status"])

    op.create_table(
        "memory_threads",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "memory_object_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("memory_objects.id"),
            nullable=False,
        ),
        sa.Column(
            "thread_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("threads.id"),
            nullable=False,
        ),
        sa.Column("hlc", sa.String(128), server_default="0:0:server", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("memory_object_id", "thread_id", name="uq_memory_threads_pair"),
    )
    op.create_index("ix_memory_threads_memory_object_id", "memory_threads", ["memory_object_id"])
    op.create_index("ix_memory_threads_thread_id", "memory_threads", ["thread_id"])


def downgrade() -> None:
    op.drop_index("ix_memory_threads_thread_id", table_name="memory_threads")
    op.drop_index("ix_memory_threads_memory_object_id", table_name="memory_threads")
    op.drop_table("memory_threads")
    op.drop_index("ix_threads_status", table_name="threads")
    op.drop_index("ix_threads_user_id", table_name="threads")
    op.drop_table("threads")
    op.drop_column("memory_facts", "embedding")
    op.drop_column("memory_facts", "expires_at")
    op.drop_column("memory_facts", "expected_volatility")
    op.drop_column("memory_facts", "last_reaffirmed_at")
    op.drop_column("memory_facts", "mention_count")
