import pytest
from sqlalchemy import select

from app.db import session_scope
from app.models import AuditLog, Document, Status
from app.status import ALLOWED, InvalidTransition, transition

pytestmark = pytest.mark.db


def _doc(session, status=Status.RECEIVED) -> Document:
    doc = Document(
        sha256="a" * 64,
        filename="x.pdf",
        size_bytes=1,
        mime="application/pdf",
        source="upload",
        status=status,
        retry_count=0,
    )
    session.add(doc)
    session.flush()
    return doc


def test_transition_changes_status_and_writes_audit_row(db):
    with session_scope() as session:
        doc = _doc(session)
        transition(session, doc, Status.PARSING, "worker:parse", {"attempt": 1})

    with session_scope() as session:
        entry = session.scalars(select(AuditLog)).one()
        assert session.get(Document, doc.id).status == Status.PARSING
        assert (entry.from_status, entry.to_status) == ("received", "parsing")
        assert entry.actor == "worker:parse"
        assert entry.detail == {"attempt": 1}


def test_transition_refuses_a_jump_that_is_not_allowed(db):
    with session_scope() as session:
        doc = _doc(session)
        with pytest.raises(InvalidTransition):
            transition(session, doc, Status.PARSED, "test")  # must go through parsing first
        assert doc.status == Status.RECEIVED


def test_parsed_moves_on_to_extracting_and_never_back_to_parsing(db):
    with session_scope() as session:
        doc = _doc(session, Status.PARSED)
        with pytest.raises(InvalidTransition):
            transition(session, doc, Status.PARSING, "test")
        transition(session, doc, Status.EXTRACTING, "test")
        transition(session, doc, Status.NEEDS_REVIEW, "test")


def test_every_status_has_a_rule():
    assert set(ALLOWED) == set(Status)
