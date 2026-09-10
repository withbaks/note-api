"""Pipeline hardening: content_hash, AI job checkpoints, fact history, usage tracking."""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "012_pipeline_hardening"
down_revision: Union[str, None] = "011_search_documents_standalone"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("memory_objects", sa.Column("content_hash", sa.String(64), nullable=True))
    op.create_index("ix_memory_objects_content_hash", "memory_objects", ["content_hash"])

    op.add_column("ai_jobs", sa.Column("last_completed_step", sa.String(32), nullable=True))
    op.add_column(
        "ai_jobs",
        sa.Column("llm_result_cache", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )

    op.add_column("users", sa.Column("helper_trust", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column("users", sa.Column("ai_monthly_cap_usd", sa.Float(), nullable=True))

    op.create_table(
        "fact_history",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("fact_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("memory_facts.id"), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("source_memory_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("changed_by", sa.String(32), server_default="ai"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_fact_history_fact_id", "fact_history", ["fact_id"])

    op.create_table(
        "ai_usage_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("memory_object_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("memory_objects.id"), nullable=True),
        sa.Column("operation", sa.String(64), nullable=False),
        sa.Column("model", sa.String(128), nullable=False),
        sa.Column("input_tokens", sa.Integer(), server_default="0"),
        sa.Column("output_tokens", sa.Integer(), server_default="0"),
        sa.Column("cost_usd", sa.Float(), server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_ai_usage_events_user_id", "ai_usage_events", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_ai_usage_events_user_id", table_name="ai_usage_events")
    op.drop_table("ai_usage_events")
    op.drop_index("ix_fact_history_fact_id", table_name="fact_history")
    op.drop_table("fact_history")
    op.drop_column("users", "ai_monthly_cap_usd")
    op.drop_column("users", "helper_trust")
    op.drop_column("ai_jobs", "llm_result_cache")
    op.drop_column("ai_jobs", "last_completed_step")
    op.drop_index("ix_memory_objects_content_hash", table_name="memory_objects")
    op.drop_column("memory_objects", "content_hash")
