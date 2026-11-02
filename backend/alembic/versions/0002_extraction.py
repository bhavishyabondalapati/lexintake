"""Extraction: new statuses, doc_type columns, extractions and llm_calls tables.

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-01 19:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_STATUSES = "'received', 'parsing', 'parsed', 'failed'"
NEW_STATUSES = (
    "'received', 'parsing', 'parsed', 'extracting', 'extracted', 'needs_review', 'failed'"
)
DOC_TYPES = "'nda', 'side_letter', 'investor_notice', 'legal_notice', 'other'"
SUBTYPES = (
    "'capital_call', 'distribution', 'annual_meeting', 'demand_letter', 'notice_of_breach', "
    "'subpoena', 'invoice', 'newsletter', 'board_minutes', 'marketing_flyer'"
)


def _status_check(statuses: str) -> None:
    op.drop_constraint(op.f("ck_documents_status_valid"), "documents", type_="check")
    op.create_check_constraint(
        op.f("ck_documents_status_valid"), "documents", f"status IN ({statuses})"
    )


def upgrade() -> None:
    _status_check(NEW_STATUSES)

    op.add_column("documents", sa.Column("doc_type", sa.String(length=30), nullable=True))
    op.add_column("documents", sa.Column("doc_subtype", sa.String(length=30), nullable=True))
    op.create_check_constraint(
        op.f("ck_documents_doc_type_valid"),
        "documents",
        f"doc_type IS NULL OR doc_type IN ({DOC_TYPES})",
    )
    op.create_check_constraint(
        op.f("ck_documents_doc_subtype_valid"),
        "documents",
        f"doc_subtype IS NULL OR doc_subtype IN ({SUBTYPES})",
    )

    op.create_table(
        "extractions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("doc_type", sa.String(length=30), nullable=True),
        sa.Column("doc_subtype", sa.String(length=30), nullable=True),
        sa.Column("classification", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("fields", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("review_reasons", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("prompt_version", sa.String(length=20), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name=op.f("fk_extractions_document_id_documents"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_extractions")),
        sa.UniqueConstraint("document_id", name=op.f("uq_extractions_document_id")),
    )

    op.create_table(
        "llm_calls",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=True),
        sa.Column("step", sa.String(length=20), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("model", sa.String(length=100), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("cache_write_tokens", sa.Integer(), nullable=False),
        sa.Column("cache_read_tokens", sa.Integer(), nullable=False),
        sa.Column("cost_usd", sa.Numeric(precision=12, scale=6), nullable=False),
        sa.Column("stop_reason", sa.String(length=30), nullable=True),
        sa.Column("outcome", sa.String(length=20), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("request_id", sa.String(length=100), nullable=True),
        sa.Column("raw_output", sa.Text(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("step IN ('classify', 'extract')", name=op.f("ck_llm_calls_step_valid")),
        sa.CheckConstraint(
            "outcome IN ('ok', 'invalid_output', 'refused', 'api_error')",
            name=op.f("ck_llm_calls_outcome_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name=op.f("fk_llm_calls_document_id_documents"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_llm_calls")),
    )
    op.create_index(op.f("ix_llm_calls_document_id"), "llm_calls", ["document_id"], unique=False)
    op.create_index(op.f("ix_llm_calls_run_id"), "llm_calls", ["run_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_llm_calls_run_id"), table_name="llm_calls")
    op.drop_index(op.f("ix_llm_calls_document_id"), table_name="llm_calls")
    op.drop_table("llm_calls")
    op.drop_table("extractions")
    op.drop_constraint(op.f("ck_documents_doc_subtype_valid"), "documents", type_="check")
    op.drop_constraint(op.f("ck_documents_doc_type_valid"), "documents", type_="check")
    op.drop_column("documents", "doc_subtype")
    op.drop_column("documents", "doc_type")
    # Documents in a Phase 3 status can't stay; hand them back as parsed.
    op.execute(
        "UPDATE documents SET status = 'parsed' "
        "WHERE status IN ('extracting', 'extracted', 'needs_review')"
    )
    _status_check(OLD_STATUSES)
