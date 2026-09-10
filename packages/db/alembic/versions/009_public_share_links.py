"""Public share links for notes.

Revision ID: 009_public_share_links
Revises: 008_admin_rbac_audit
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "009_public_share_links"
down_revision: Union[str, None] = "008_admin_rbac_audit"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "public_share_links",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "memory_object_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("memory_objects.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("token", sa.String(64), nullable=False, unique=True),
        sa.Column(
            "created_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_public_share_links_token", "public_share_links", ["token"])
    op.create_index("ix_public_share_links_memory_object_id", "public_share_links", ["memory_object_id"])


def downgrade() -> None:
    op.drop_index("ix_public_share_links_memory_object_id", table_name="public_share_links")
    op.drop_index("ix_public_share_links_token", table_name="public_share_links")
    op.drop_table("public_share_links")
