"""Playbook check: auto_approved status and extractions.playbook_results.

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-02 23:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_STATUSES = (
    "'received', 'parsing', 'parsed', 'extracting', 'extracted', 'needs_review', 'failed'"
)
NEW_STATUSES = (
    "'received', 'parsing', 'parsed', 'extracting', 'extracted', 'needs_review', "
    "'auto_approved', 'failed'"
)


def _status_check(statuses: str) -> None:
    op.drop_constraint(op.f("ck_documents_status_valid"), "documents", type_="check")
    op.create_check_constraint(
        op.f("ck_documents_status_valid"), "documents", f"status IN ({statuses})"
    )


def upgrade() -> None:
    _status_check(NEW_STATUSES)
    # [{rule_id, field, outcome, value, page, quote, reason}]; null until the playbook has run
    op.add_column(
        "extractions",
        sa.Column("playbook_results", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("extractions", "playbook_results")
    # An auto-approved document was extracted; hand it back as such.
    op.execute("UPDATE documents SET status = 'extracted' WHERE status = 'auto_approved'")
    _status_check(OLD_STATUSES)
