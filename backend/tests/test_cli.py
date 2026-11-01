import pytest
from sqlalchemy import select

from app import cli
from app.db import session_scope
from app.intake import ingest_file
from app.models import Document
from app.storage.blobs import LocalBlobStore
from tests.fakes import RecordingQueue

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
