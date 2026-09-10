"""Widen users.avatar_url for longer URIs."""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "004_avatar_url_widen"
down_revision: Union[str, None] = "003_memory_types_media"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "users",
        "avatar_url",
        existing_type=sa.String(length=512),
        type_=sa.String(length=2048),
        existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "users",
        "avatar_url",
        existing_type=sa.String(length=2048),
        type_=sa.String(length=512),
        existing_nullable=True,
    )
