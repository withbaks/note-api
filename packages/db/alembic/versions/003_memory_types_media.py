"""Add image/bookmark memory types."""

from typing import Sequence, Union

from alembic import op

revision: str = "003_memory_types_media"
down_revision: Union[str, None] = "002_social_ingest"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TYPE memorytype ADD VALUE IF NOT EXISTS 'image'")
    op.execute("ALTER TYPE memorytype ADD VALUE IF NOT EXISTS 'bookmark'")


def downgrade() -> None:
    # Postgres cannot easily remove enum values
    pass
