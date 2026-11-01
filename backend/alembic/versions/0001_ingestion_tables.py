"""Ingestion tables: documents, text_blocks, audit_log.

Revision ID: 0001
Revises:
Create Date: 2026-11-01 16:39:35.197621
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "documents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("mime", sa.String(length=100), nullable=False),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "source IN ('upload', 'watcher')", name=op.f("ck_documents_source_valid")
        ),
        sa.CheckConstraint(
            "status IN ('received', 'parsing', 'parsed', 'failed')",
            name=op.f("ck_documents_status_valid"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_documents")),
        sa.UniqueConstraint("sha256", name=op.f("uq_documents_sha256")),
    )
    op.create_table(
        "audit_log",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("from_status", sa.String(length=30), nullable=True),
        sa.Column("to_status", sa.String(length=30), nullable=False),
        sa.Column("actor", sa.String(length=60), nullable=False),
        sa.Column("detail", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name=op.f("fk_audit_log_document_id_documents"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_log")),
    )
    op.create_index(op.f("ix_audit_log_document_id"), "audit_log", ["document_id"], unique=False)
    op.create_table(
        "text_blocks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("page", sa.Integer(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("x0", sa.Float(), nullable=False),
        sa.Column("y0", sa.Float(), nullable=False),
        sa.Column("x1", sa.Float(), nullable=False),
        sa.Column("y1", sa.Float(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("label", sa.String(length=40), nullable=False),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column("ocr_confidence", sa.Float(), nullable=True),
        sa.CheckConstraint(
            "source IN ('text_layer', 'ocr')", name=op.f("ck_text_blocks_source_valid")
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name=op.f("fk_text_blocks_document_id_documents"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_text_blocks")),
    )
    op.create_index(
        op.f("ix_text_blocks_document_id_page_seq"),
        "text_blocks",
        ["document_id", "page", "seq"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_text_blocks_document_id_page_seq"), table_name="text_blocks")
    op.drop_table("text_blocks")
    op.drop_index(op.f("ix_audit_log_document_id"), table_name="audit_log")
    op.drop_table("audit_log")
    op.drop_table("documents")
