"""Link metadata on memories and shared link preview cache."""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "014_link_metadata"
down_revision: Union[str, None] = "013_threads_fact_resolution"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "memory_objects",
        sa.Column("link_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.create_table(
        "link_preview_cache",
        sa.Column("url_hash", sa.String(length=64), nullable=False),
        sa.Column("url", sa.String(length=2048), nullable=False),
        sa.Column("preview", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("url_hash"),
    )


def downgrade() -> None:
    op.drop_table("link_preview_cache")
    op.drop_column("memory_objects", "link_metadata")
