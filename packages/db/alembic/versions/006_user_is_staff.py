"""Add is_staff flag for admin dashboard access."""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "006_user_is_staff"
down_revision: Union[str, None] = "005_people_semantic_types"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("is_staff", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.create_index("ix_users_is_staff", "users", ["is_staff"])


def downgrade() -> None:
    op.drop_index("ix_users_is_staff", table_name="users")
    op.drop_column("users", "is_staff")
