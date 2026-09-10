"""Smart helpers, facts metadata, contact links, tool runs.

Revision ID: 007_smart_helpers_tools
Revises: 006_user_is_staff
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "007_smart_helpers_tools"
down_revision: Union[str, None] = "006_user_is_staff"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("helpers", sa.Column("kind", sa.String(32), server_default="knowledge", nullable=False))
    op.add_column("helpers", sa.Column("title", sa.String(256), nullable=True))
    op.add_column("helpers", sa.Column("description", sa.Text(), nullable=True))
    op.add_column("helpers", sa.Column("proposed_fact_key", sa.String(256), nullable=True))
    op.add_column("helpers", sa.Column("span_start", sa.Integer(), nullable=True))
    op.add_column("helpers", sa.Column("span_end", sa.Integer(), nullable=True))
    op.add_column("helpers", sa.Column("confidence", sa.Float(), nullable=True))
    op.add_column("helpers", sa.Column("metadata", postgresql.JSONB(), nullable=True))
    op.add_column("helpers", sa.Column("tool_name", sa.String(64), nullable=True))

    op.execute("UPDATE helpers SET title = key WHERE title IS NULL")
    op.alter_column("helpers", "title", nullable=False)

    op.add_column("memory_facts", sa.Column("display_label", sa.String(256), nullable=True))
    op.add_column("memory_facts", sa.Column("fact_type", sa.String(32), server_default="attribute", nullable=False))

    op.add_column("people", sa.Column("source", sa.String(32), server_default="inferred", nullable=False))
    op.add_column("people", sa.Column("contact_link_id", sa.String(128), nullable=True))

    op.create_table(
        "tool_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("helper_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("helpers.id"), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("tool_name", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), server_default="pending", nullable=False),
        sa.Column("result", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_tool_runs_helper_id", "tool_runs", ["helper_id"])


def downgrade() -> None:
    op.drop_index("ix_tool_runs_helper_id", table_name="tool_runs")
    op.drop_table("tool_runs")
    op.drop_column("people", "contact_link_id")
    op.drop_column("people", "source")
    op.drop_column("memory_facts", "fact_type")
    op.drop_column("memory_facts", "display_label")
    op.drop_column("helpers", "tool_name")
    op.drop_column("helpers", "metadata")
    op.drop_column("helpers", "confidence")
    op.drop_column("helpers", "span_end")
    op.drop_column("helpers", "span_start")
    op.drop_column("helpers", "proposed_fact_key")
    op.drop_column("helpers", "description")
    op.drop_column("helpers", "kind")
    op.drop_column("helpers", "title")
