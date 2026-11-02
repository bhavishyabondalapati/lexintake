"""Review: a human-approved status.

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-03 10:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_STATUSES = (
    "'received', 'parsing', 'parsed', 'extracting', 'extracted', 'needs_review', "
    "'auto_approved', 'failed'"
)
NEW_STATUSES = OLD_STATUSES.replace("'failed'", "'approved', 'failed'")


def _status_check(statuses: str) -> None:
    op.drop_constraint(op.f("ck_documents_status_valid"), "documents", type_="check")
    op.create_check_constraint(
        op.f("ck_documents_status_valid"), "documents", f"status IN ({statuses})"
    )


def upgrade() -> None:
    _status_check(NEW_STATUSES)


def downgrade() -> None:
    # A person approved these after review; the closest older state is needs_review.
    op.execute("UPDATE documents SET status = 'needs_review' WHERE status = 'approved'")
    _status_check(OLD_STATUSES)
