"""People + semantic types: first-class contacts and typed facts/helpers."""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "005_people_semantic_types"
down_revision: Union[str, None] = "004_avatar_url_widen"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "people",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("display_name", sa.String(128), nullable=False),
        sa.Column("normalized_name", sa.String(128), nullable=False),
        sa.Column("relationship", sa.String(64), nullable=True),
        sa.Column("aliases", postgresql.JSONB(), nullable=True),
        sa.Column("merged_into_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("people.id"), nullable=True),
        sa.Column("hlc", sa.String(128), server_default="0:0:server", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("user_id", "normalized_name", name="uq_people_user_normalized_name"),
    )
    op.create_index("ix_people_user_id", "people", ["user_id"])
    op.create_index("ix_people_normalized_name", "people", ["normalized_name"])
    op.create_index("ix_people_user_relationship", "people", ["user_id", "relationship"])

    op.add_column("helpers", sa.Column("person_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("helpers", sa.Column("semantic_type", sa.String(64), nullable=True))
    op.create_foreign_key("fk_helpers_person_id", "helpers", "people", ["person_id"], ["id"])
    op.create_index("ix_helpers_person_id", "helpers", ["person_id"])

    op.add_column("memory_facts", sa.Column("person_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("memory_facts", sa.Column("semantic_type", sa.String(64), nullable=True))
    op.create_foreign_key("fk_memory_facts_person_id", "memory_facts", "people", ["person_id"], ["id"])
    op.create_index("ix_memory_facts_person_id", "memory_facts", ["person_id"])

    op.drop_constraint("uq_memory_facts_user_key_person", "memory_facts", type_="unique")
    op.create_unique_constraint(
        "uq_memory_facts_user_key_person_id",
        "memory_facts",
        ["user_id", "key", "person_id"],
    )

    op.create_table(
        "memory_people",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "memory_object_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("memory_objects.id"),
            nullable=False,
        ),
        sa.Column("person_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("people.id"), nullable=False),
        sa.Column("role", sa.String(32), server_default="mentioned", nullable=False),
        sa.Column("hlc", sa.String(128), server_default="0:0:server", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("memory_object_id", "person_id", "role", name="uq_memory_people_triple"),
    )
    op.create_index("ix_memory_people_memory_object_id", "memory_people", ["memory_object_id"])
    op.create_index("ix_memory_people_person_id", "memory_people", ["person_id"])


def downgrade() -> None:
    op.drop_table("memory_people")
    op.drop_constraint("uq_memory_facts_user_key_person_id", "memory_facts", type_="unique")
    op.create_unique_constraint(
        "uq_memory_facts_user_key_person",
        "memory_facts",
        ["user_id", "key", "person"],
    )
    op.drop_constraint("fk_memory_facts_person_id", "memory_facts", type_="foreignkey")
    op.drop_index("ix_memory_facts_person_id", "memory_facts")
    op.drop_column("memory_facts", "semantic_type")
    op.drop_column("memory_facts", "person_id")
    op.drop_constraint("fk_helpers_person_id", "helpers", type_="foreignkey")
    op.drop_index("ix_helpers_person_id", "helpers")
    op.drop_column("helpers", "semantic_type")
    op.drop_column("helpers", "person_id")
    op.drop_table("people")
