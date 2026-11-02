"""The one place that changes a document's status. Every change writes an audit_log row in the
same transaction, so the log can never disagree with the documents table."""

from typing import Any

from sqlalchemy.orm import Session

from app.models import AuditLog, Document, Status

# Which status can follow which. `parsing -> parsing` and `extracting -> extracting` are
# redeliveries after a worker crash. `failed -> parsing | extracting` is a manual requeue of the
# step that failed. `extracting -> parsed` hands a document back untouched when the spend cap
# stops a run. `extracted | needs_review | auto_approved -> extracting` is a deliberate re-run
# (`--force`). `extracted -> auto_approved | needs_review` is the playbook decision for an NDA.
ALLOWED: dict[Status, set[Status]] = {
    Status.RECEIVED: {Status.PARSING, Status.FAILED},
    Status.PARSING: {Status.PARSING, Status.PARSED, Status.FAILED},
    Status.PARSED: {Status.EXTRACTING},
    Status.EXTRACTING: {
        Status.EXTRACTING,
        Status.EXTRACTED,
        Status.NEEDS_REVIEW,
        Status.FAILED,
        Status.PARSED,
    },
    Status.EXTRACTED: {
        Status.EXTRACTING,
        Status.AUTO_APPROVED,
        Status.NEEDS_REVIEW,
        Status.APPROVED,
    },
    Status.NEEDS_REVIEW: {Status.EXTRACTING, Status.APPROVED},
    Status.APPROVED: {Status.EXTRACTING},
    Status.AUTO_APPROVED: {Status.EXTRACTING},
    Status.FAILED: {Status.PARSING, Status.EXTRACTING},
}


class InvalidTransition(Exception):
    pass


def audit(
    session: Session,
    doc: Document,
    actor: str,
    detail: dict[str, Any] | None = None,
    *,
    from_status: str | None,
    to_status: str,
) -> AuditLog:
    entry = AuditLog(
        document_id=doc.id,
        from_status=from_status,
        to_status=to_status,
        actor=actor,
        detail=detail or {},
    )
    session.add(entry)
    return entry


def transition(
    session: Session, doc: Document, to: Status, actor: str, detail: dict[str, Any] | None = None
) -> None:
    current = Status(doc.status)
    if to not in ALLOWED[current]:
        raise InvalidTransition(f"{doc.id}: {current} -> {to} is not allowed")
    doc.status = to
    audit(session, doc, actor, detail, from_status=current, to_status=to)
