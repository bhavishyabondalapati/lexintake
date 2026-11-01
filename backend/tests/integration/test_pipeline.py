"""Intake + parse task end to end, with real Docling and a real database (no Celery broker:
the queue is a list, and the test runs each queued task itself)."""

import json

import pytest
from sqlalchemy import func, select

from app.db import session_scope
from app.intake import ingest_file
from app.models import AuditLog, Document, TextBlock
from app.storage.blobs import LocalBlobStore
from app.tasks.parse import run_parse
from tests.conftest import FIXTURES
from tests.fakes import RecordingQueue

pytestmark = [pytest.mark.slow, pytest.mark.db]

MANIFEST = {
    d["doc_id"]: d for d in json.loads((FIXTURES / "manifest.json").read_text())["documents"]
}


@pytest.fixture(scope="module")
def parser():
    from app.parsing.docling_parser import DoclingParser

    p = DoclingParser(device="cpu")
    p.warm_up()
    return p


@pytest.fixture
def pipeline(db, tmp_path, parser):
    blobs = LocalBlobStore(tmp_path / "blobs")
    queue = RecordingQueue()

    def ingest(doc_id: str, source: str = "upload"):
        result = ingest_file(
            FIXTURES / f"{doc_id}.pdf",
            f"{doc_id}.pdf",
            source=source,
            actor="test",
            blobs=blobs,
            enqueue=queue,
            max_bytes=50 * 1024 * 1024,
        )
        while queue.queued:
            run_parse(queue.queued.pop(0), parser, blobs, max_attempts=3)
        return result

    return ingest, blobs


def blocks_by_source(document_id):
    with session_scope() as session:
        rows = session.execute(
            select(TextBlock.source, func.count())
            .where(TextBlock.document_id == document_id)
            .group_by(TextBlock.source)
        ).all()
        return dict(rows)


def test_digital_pdf_ends_up_parsed_with_text_layer_blocks(pipeline):
    ingest, _ = pipeline
    doc_id = ingest("syn_00001").document.id

    with session_scope() as session:
        doc = session.get_one(Document, doc_id)
        assert (doc.status, doc.page_count, doc.error) == ("parsed", 1, None)
    assert set(blocks_by_source(doc_id)) == {"text_layer"}


def test_scanned_pdf_ends_up_parsed_with_ocr_blocks(pipeline):
    ingest, _ = pipeline
    doc_id = ingest("syn_00002").document.id

    with session_scope() as session:
        assert session.get_one(Document, doc_id).status == "parsed"
        conf = session.scalars(
            select(TextBlock.ocr_confidence).where(TextBlock.document_id == doc_id)
        ).all()
    assert set(blocks_by_source(doc_id)) == {"ocr"}
    assert all(c is not None for c in conf)


def test_duplicate_returns_the_first_document_and_is_parsed_once(pipeline):
    ingest, blobs = pipeline
    assert MANIFEST["syn_00246"]["duplicate_of"] == "syn_00251"
    first = ingest("syn_00251")

    second = ingest("syn_00246", source="watcher")

    assert second.duplicate is True
    assert second.document.id == first.document.id
    with session_scope() as session:
        assert session.scalar(select(func.count()).select_from(Document)) == 1
        trail = session.execute(
            select(AuditLog.from_status, AuditLog.to_status, AuditLog.actor).order_by(AuditLog.id)
        ).all()
    parses = [t for t in trail if t.to_status == "parsing"]
    assert len(parses) == 1
    assert trail[-1].from_status == trail[-1].to_status == "parsed"  # the duplicate's entry
    assert len(list(blobs.root.rglob("*.pdf"))) == 1
