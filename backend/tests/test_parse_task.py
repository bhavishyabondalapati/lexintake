"""The parse task, with a fake parser (no Docling)."""

import pytest
from sqlalchemy import func, select

from app.config import get_settings
from app.db import _lock_key, session_scope
from app.intake import ingest_file
from app.models import AuditLog, Document, TextBlock
from app.parsing.base import ParseError
from app.storage.blobs import LocalBlobStore
from app.tasks.parse import run_parse
from tests.fakes import FakeParser, RecordingQueue

pytestmark = pytest.mark.db


@pytest.fixture
def blobs(tmp_path):
    return LocalBlobStore(tmp_path / "blobs")


@pytest.fixture
def doc_id(db, blobs, make_pdf):
    result = ingest_file(
        make_pdf(),
        "doc.pdf",
        source="upload",
        actor="test",
        blobs=blobs,
        enqueue=RecordingQueue(),
        max_bytes=10_000,
    )
    return result.document.id


def state(doc_id):
    with session_scope() as session:
        doc = session.get_one(Document, doc_id)
        blocks = session.scalar(
            select(func.count()).select_from(TextBlock).where(TextBlock.document_id == doc_id)
        )
        trail = [
            (a.from_status, a.to_status)
            for a in session.scalars(
                select(AuditLog).where(AuditLog.document_id == doc_id).order_by(AuditLog.id)
            )
        ]
        return doc, blocks, trail


def test_received_document_is_parsed_and_every_step_is_audited(doc_id, blobs):
    parser = FakeParser(pages=2)

    assert run_parse(doc_id, parser, blobs, max_attempts=3) == "parsed"

    doc, blocks, trail = state(doc_id)
    assert doc.status == "parsed"
    assert doc.page_count == 2
    assert doc.error is None
    assert doc.retry_count == 1
    assert blocks == 4
    assert trail == [(None, "received"), ("received", "parsing"), ("parsing", "parsed")]
    assert parser.calls == [blobs.path(doc.sha256)]


def test_running_twice_does_nothing_the_second_time(doc_id, blobs):
    parser = FakeParser()
    run_parse(doc_id, parser, blobs, max_attempts=3)

    assert run_parse(doc_id, parser, blobs, max_attempts=3) == "already_parsed"

    _, blocks, trail = state(doc_id)
    assert blocks == 2
    assert len(trail) == 3
    assert len(parser.calls) == 1


def test_redelivery_after_a_crash_replaces_blocks_instead_of_duplicating(doc_id, blobs):
    run_parse(doc_id, FakeParser(), blobs, max_attempts=3)
    # Simulate a worker that died after saving blocks but before the queue heard back:
    with session_scope() as session:
        session.get_one(Document, doc_id).status = "parsing"

    assert run_parse(doc_id, FakeParser(), blobs, max_attempts=3) == "parsed"

    doc, blocks, _ = state(doc_id)
    assert blocks == 2
    assert doc.retry_count == 2


def test_parse_error_marks_failed_and_saves_the_error(doc_id, blobs):
    parser = FakeParser(error=ParseError("cannot open PDF: encrypted"))

    assert run_parse(doc_id, parser, blobs, max_attempts=3) == "failed"

    doc, blocks, trail = state(doc_id)
    assert doc.status == "failed"
    assert doc.error == "ParseError: cannot open PDF: encrypted"
    assert blocks == 0
    assert trail[-1] == ("parsing", "failed")


def test_failed_document_can_be_retried(doc_id, blobs):
    run_parse(doc_id, FakeParser(error=RuntimeError("boom")), blobs, max_attempts=3)

    assert run_parse(doc_id, FakeParser(), blobs, max_attempts=3) == "parsed"
    doc, _, trail = state(doc_id)
    assert doc.error is None
    assert ("failed", "parsing") in trail


def test_gives_up_after_max_attempts(doc_id, blobs):
    # A PDF that kills the worker never reaches the error handler; each attempt is counted
    # up front, so after max_attempts redeliveries it is marked failed instead of looping.
    with session_scope() as session:
        doc = session.get_one(Document, doc_id)
        doc.status = "parsing"
        doc.retry_count = 3

    assert run_parse(doc_id, FakeParser(), blobs, max_attempts=3) == "gave_up"

    doc, _, trail = state(doc_id)
    assert doc.status == "failed"
    assert doc.error == "gave up after 3 attempts"
    assert trail[-1] == ("parsing", "failed")


def test_partial_parse_is_parsed_with_the_problem_noted(doc_id, blobs):
    assert run_parse(doc_id, FakeParser(pages=3, failed_pages=[2]), blobs, 3) == "parsed"

    doc, _, _ = state(doc_id)
    assert doc.status == "parsed"
    assert doc.error == "partial: pages [2] could not be parsed"


def test_skips_a_document_another_worker_is_parsing(doc_id, blobs, db):
    with db.connect() as other_worker:
        other_worker.scalar(select(func.pg_advisory_lock(_lock_key(doc_id))))

        assert run_parse(doc_id, FakeParser(), blobs, max_attempts=3) == "locked"

        other_worker.scalar(select(func.pg_advisory_unlock(_lock_key(doc_id))))
    assert state(doc_id)[0].status == "received"


def test_unknown_document_is_reported_missing(db, blobs):
    import uuid

    assert run_parse(uuid.uuid4(), FakeParser(), blobs, max_attempts=3) == "missing"


def test_parsed_document_is_queued_for_extraction_only_when_turned_on(
    doc_id, blobs, make_pdf, monkeypatch
):
    queued = RecordingQueue()
    monkeypatch.setattr("app.tasks.parse.enqueue_extract", queued)

    run_parse(doc_id, FakeParser(), blobs, max_attempts=3)
    assert queued.queued == []  # EXTRACT_ON_PARSE is off by default: no API spend on upload

    monkeypatch.setenv("EXTRACT_ON_PARSE", "true")
    get_settings.cache_clear()
    other = ingest_file(
        make_pdf(b"second"),
        "b.pdf",
        source="upload",
        actor="test",
        blobs=blobs,
        enqueue=RecordingQueue(),
        max_bytes=10_000,
    ).document.id
    try:
        run_parse(other, FakeParser(), blobs, max_attempts=3)
    finally:
        get_settings.cache_clear()
    assert queued.queued == [other]
