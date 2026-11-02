from decimal import Decimal

import pytest
from sqlalchemy import select

from app import cli
from app.config import Settings
from app.db import session_scope
from app.extraction.base import LlmCallFailed, Usage
from app.intake import ingest_file
from app.models import Document, Status
from app.status import transition
from app.storage.blobs import LocalBlobStore
from tests.docs import NDA, add_parsed_doc
from tests.fakes import FakeExtractor, RecordingQueue

pytestmark = pytest.mark.db


def test_requeue_queues_matching_documents_and_resets_failed_attempts(
    db, tmp_path, make_pdf, monkeypatch
):
    blobs = LocalBlobStore(tmp_path / "blobs")
    for i, status in enumerate(["received", "failed", "parsed"]):
        doc = ingest_file(
            make_pdf(str(i).encode(), f"{i}.pdf"),
            f"{i}.pdf",
            source="upload",
            actor="test",
            blobs=blobs,
            enqueue=RecordingQueue(),
            max_bytes=10_000,
        ).document
        with session_scope() as session:
            row = session.get_one(Document, doc.id)
            row.status, row.retry_count = status, 3
    queue = RecordingQueue()
    monkeypatch.setattr(cli, "enqueue_parse", queue)

    assert cli.requeue(["received", "failed"]) == 2

    assert len(queue.queued) == 2
    with session_scope() as session:
        counts = {d.status: d.retry_count for d in session.scalars(select(Document))}
    assert counts == {"received": 3, "failed": 0, "parsed": 3}


def test_requeue_sends_a_document_that_failed_during_extraction_back_to_extraction(db, monkeypatch):
    doc_id = add_parsed_doc(NDA)
    with session_scope() as session:
        doc = session.get_one(Document, doc_id)
        transition(session, doc, Status.EXTRACTING, "test")
        transition(session, doc, Status.FAILED, "test")
    parse_queue, extract_queue = RecordingQueue(), RecordingQueue()
    monkeypatch.setattr(cli, "enqueue_parse", parse_queue)
    monkeypatch.setattr(cli, "enqueue_extract", extract_queue)

    assert cli.requeue(["failed"]) == 1

    assert (parse_queue.queued, extract_queue.queued) == ([], [doc_id])


def _settings():
    return Settings(
        _env_file=None,
        anthropic_model_classify="claude-haiku-5-5",
        anthropic_model_extract="claude-sonnet-5-5",
    )


def test_extract_respects_limit_and_records_cost_per_model(db):
    ids = [add_parsed_doc(NDA) for _ in range(3)]

    run = cli.extract(FakeExtractor(NDA), _settings(), limit=2, max_spend="1.00")

    assert list(run.results) == ids[:2]
    assert set(run.results.values()) == {"auto_approved"}
    table = {(r["step"], r["model"]): r for r in cli.cost_table(run.run_id)}
    assert table[("classify", "claude-haiku-5-5")]["documents"] == 2
    assert table[("extract", "claude-sonnet-5-5")]["calls"] == 2
    assert sum(r["cost_usd"] for r in table.values()) == run.spent
    with session_scope() as session:
        assert session.get_one(Document, ids[2]).status == Status.PARSED


def test_extract_stops_at_the_spend_limit_and_leaves_the_rest_parsed(db):
    ids = [add_parsed_doc(NDA) for _ in range(3)]

    # Expensive answers (~$0.08 per NDA extraction): the first NDA fits under $0.15, then the
    # second extraction's worst case (~$0.09) would not.
    fake = FakeExtractor(NDA, usage=Usage(input_tokens=5_000, output_tokens=7_000))
    run = cli.extract(fake, _settings(), max_spend="0.15")

    assert run.stopped_by_spend_limit
    assert list(run.results.values()) == ["auto_approved", "spend_limit"]
    assert run.spent <= Decimal("0.15")
    with session_scope() as session:
        statuses = [session.get_one(Document, i).status for i in ids]
    assert statuses == [Status.AUTO_APPROVED, Status.PARSED, Status.PARSED]


def test_extract_marks_a_document_failed_when_the_api_keeps_failing(db):
    doc_id = add_parsed_doc(NDA)
    fake = FakeExtractor(NDA, error=LlmCallFailed("OverloadedError 529", retryable=True))

    run = cli.extract(fake, _settings())

    assert run.results == {doc_id: "failed"}
    with session_scope() as session:
        assert "529" in session.get_one(Document, doc_id).error


def test_extract_command_without_models_fails_documents_with_a_clear_message(db, monkeypatch):
    doc_id = add_parsed_doc(NDA)
    monkeypatch.setattr(cli, "get_settings", _settings_without_models)
    monkeypatch.setattr("app.extraction.anthropic_extractor.anthropic.Anthropic", lambda **_: None)

    cli.main(["extract", "--limit", "1"])

    with session_scope() as session:
        assert "is not set" in session.get_one(Document, doc_id).error


def _settings_without_models():
    return Settings(_env_file=None)
