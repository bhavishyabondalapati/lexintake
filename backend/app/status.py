"""The one place that changes a document's status. Every change writes an audit_log row in the
same transaction, so the log can never disagree with the documents table."""

from typing import Any

from sqlalchemy.orm import Session

from app.models import AuditLog, Document, Status

# Which status can follow which. `parsing -> parsing` is a redelivery after a worker crash, and
# `failed -> parsing` is a manual requeue.
ALLOWED: dict[Status, set[Status]] = {
    Status.RECEIVED: {Status.PARSING, Status.FAILED},
    Status.PARSING: {Status.PARSING, Status.PARSED, Status.FAILED},
    Status.FAILED: {Status.PARSING},
    Status.PARSED: set(),
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
