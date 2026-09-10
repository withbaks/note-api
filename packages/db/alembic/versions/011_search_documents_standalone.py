"""Allow standalone search documents (people, facts, friendships)."""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "011_search_documents_standalone"
down_revision: Union[str, None] = "010_search_documents"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "search_documents",
        "memory_object_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=True,
    )


def downgrade() -> None:
    op.execute("DELETE FROM search_documents WHERE memory_object_id IS NULL")
    op.alter_column(
        "search_documents",
        "memory_object_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=False,
    )
