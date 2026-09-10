"""Admin RBAC and audit logs.

Revision ID: 008_admin_rbac_audit
Revises: 007_smart_helpers_tools
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "008_admin_rbac_audit"
down_revision: Union[str, None] = "007_smart_helpers_tools"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

admin_role_enum = postgresql.ENUM("viewer", "support", "admin", name="adminrole", create_type=False)


def upgrade() -> None:
    op.execute("CREATE TYPE adminrole AS ENUM ('viewer', 'support', 'admin')")
    op.add_column(
        "users",
        sa.Column(
            "admin_role",
            admin_role_enum,
            nullable=True,
        ),
    )
    op.execute("UPDATE users SET admin_role = 'admin' WHERE is_staff = true")

    op.create_table(
        "admin_audit_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("actor_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("action", sa.String(128), nullable=False),
        sa.Column("target_type", sa.String(64), nullable=True),
        sa.Column("target_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("metadata", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_admin_audit_logs_actor_id", "admin_audit_logs", ["actor_id"])
    op.create_index("ix_admin_audit_logs_action", "admin_audit_logs", ["action"])
    op.create_index("ix_admin_audit_logs_created_at", "admin_audit_logs", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_admin_audit_logs_created_at", table_name="admin_audit_logs")
    op.drop_index("ix_admin_audit_logs_action", table_name="admin_audit_logs")
    op.drop_index("ix_admin_audit_logs_actor_id", table_name="admin_audit_logs")
    op.drop_table("admin_audit_logs")
    op.drop_column("users", "admin_role")
    op.execute("DROP TYPE adminrole")
