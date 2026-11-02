"""POST /documents and GET /documents/{id}."""

import hashlib
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.config import get_settings
from app.db import session_scope
from app.main import app
from app.models import AuditLog, Document
from app.routes.documents import get_blob_store, get_enqueue
from app.storage.blobs import LocalBlobStore
from tests.fakes import RecordingQueue

pytestmark = pytest.mark.db

PDF = b"%PDF-1.7\n1 0 obj << >> endobj\n%%EOF\n"


@pytest.fixture
def queue():
    return RecordingQueue()


@pytest.fixture
def blobs(tmp_path):
    return LocalBlobStore(tmp_path / "blobs")


@pytest.fixture
def client(db, queue, blobs):
    app.dependency_overrides[get_enqueue] = lambda: queue
    app.dependency_overrides[get_blob_store] = lambda: blobs
    yield TestClient(app)
    app.dependency_overrides.clear()


def upload(client, content=PDF, name="nda.pdf"):
    return client.post("/documents", files={"file": (name, content, "application/pdf")})


def audit_rows():
    with session_scope() as session:
        return session.scalars(select(AuditLog).order_by(AuditLog.id)).all()


def test_new_pdf_is_stored_recorded_and_queued(client, queue, blobs):
    response = upload(client)

    assert response.status_code == 201
    body = response.json()
    assert body["duplicate"] is False
    assert body["status"] == "received"
    assert body["sha256"] == hashlib.sha256(PDF).hexdigest()
    assert body["filename"] == "nda.pdf"
    assert blobs.open(body["sha256"]).read() == PDF
    assert [str(i) for i in queue.queued] == [body["id"]]
    [entry] = audit_rows()
    assert (entry.from_status, entry.to_status, entry.actor) == (None, "received", "api:upload")


def test_same_bytes_again_return_the_existing_document_without_reprocessing(client, queue):
    first = upload(client, name="first.pdf").json()

    response = upload(client, name="renamed-copy.pdf")

    assert response.status_code == 200
    body = response.json()
    assert body["duplicate"] is True
    assert body["id"] == first["id"]
    assert body["filename"] == "first.pdf"  # the original record, unchanged
    assert len(queue.queued) == 1  # no second parse
    created, dup = audit_rows()
    assert (dup.from_status, dup.to_status) == ("received", "received")
    assert dup.detail["event"] == "duplicate_received"
    assert dup.detail["filename"] == "renamed-copy.pdf"


def test_concurrent_uploads_of_the_same_file_create_one_document(client, queue):
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(pool.map(lambda _: upload(client), range(4)))

    assert sorted(r.status_code for r in responses) == [200, 200, 200, 201]
    assert len({r.json()["id"] for r in responses}) == 1
    assert len(queue.queued) == 1
    with session_scope() as session:
        assert session.scalar(select(func.count()).select_from(Document)) == 1


def test_non_pdf_is_rejected_with_415(client, queue):
    response = upload(client, content=b"PK\x03\x04 this is a zip", name="evil.pdf")

    assert response.status_code == 415
    assert queue.queued == []
    assert audit_rows() == []


def test_pdf_header_may_follow_a_short_preamble(client):
    assert upload(client, content=b"\x00\x00junk" + PDF).status_code == 201


def test_too_large_upload_is_rejected_with_413(client, monkeypatch, queue):
    monkeypatch.setenv("MAX_UPLOAD_MB", "1")
    get_settings.cache_clear()
    try:
        response = upload(client, content=PDF + b"0" * (1024 * 1024 + 1))
    finally:
        get_settings.cache_clear()

    assert response.status_code == 413
    assert queue.queued == []


def test_document_is_still_recorded_when_the_queue_is_down(db, blobs):
    app.dependency_overrides[get_enqueue] = lambda: RecordingQueue(fail=True)
    app.dependency_overrides[get_blob_store] = lambda: blobs
    try:
        response = upload(TestClient(app))
    finally:
        app.dependency_overrides.clear()

    # Stays `received`; `python -m app.cli requeue` queues it later.
    assert response.status_code == 201
    assert response.json()["status"] == "received"


def test_get_document_returns_status_and_block_count(client):
    doc_id = upload(client).json()["id"]

    response = client.get(f"/documents/{doc_id}")

    assert response.status_code == 200
    assert response.json()["status"] == "received"
    assert response.json()["block_count"] == 0


def test_get_unknown_document_is_404(client):
    assert client.get("/documents/00000000-0000-0000-0000-000000000000").status_code == 404


def test_get_document_shows_type_and_why_it_needs_review(client):
    from tests.docs import NDA, add_parsed_doc
    from tests.fakes import FakeExtractor
    from tests.test_extract_run import _run

    doc_id = add_parsed_doc(NDA)
    _run(doc_id, FakeExtractor(NDA, fields={"term_months": {"confidence": 0.5}}))

    body = client.get(f"/documents/{doc_id}").json()

    assert (body["status"], body["doc_type"], body["doc_subtype"]) == ("needs_review", "nda", None)
    assert body["review_reasons"] == [
        {"code": "low_field_confidence", "field": "term_months", "detail": "0.50 < 0.8"}
    ]
