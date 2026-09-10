"""Search documents table with vector index.

Revision ID: 010_search_documents
Revises: 009_public_share_links
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision: str = "010_search_documents"
down_revision: Union[str, None] = "009_public_share_links"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "search_documents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "memory_object_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("memory_objects.id"),
            nullable=False,
        ),
        sa.Column("doc_type", sa.String(32), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(1536), nullable=True),
        sa.Column("metadata", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_search_documents_user_id", "search_documents", ["user_id"])
    op.create_index("ix_search_documents_memory_object_id", "search_documents", ["memory_object_id"])
    op.create_index("ix_search_documents_doc_type", "search_documents", ["doc_type"])
    op.create_index(
        "ix_search_documents_user_memory",
        "search_documents",
        ["user_id", "memory_object_id"],
    )
    op.execute(
        """
        CREATE INDEX ix_search_documents_embedding_hnsw
        ON search_documents
        USING hnsw (embedding vector_cosine_ops)
        """
    )
    op.execute(
        """
        CREATE INDEX ix_search_documents_text_fts
        ON search_documents
        USING gin (to_tsvector('english', coalesce(text, '')))
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_search_documents_text_fts")
    op.execute("DROP INDEX IF EXISTS ix_search_documents_embedding_hnsw")
    op.drop_index("ix_search_documents_user_memory", table_name="search_documents")
    op.drop_index("ix_search_documents_doc_type", table_name="search_documents")
    op.drop_index("ix_search_documents_memory_object_id", table_name="search_documents")
    op.drop_index("ix_search_documents_user_id", table_name="search_documents")
    op.drop_table("search_documents")
