"""Review endpoints: queue, detail, approve, override."""

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.extraction.pricing import SpendGuard
from app.extraction.run import RunContext, run_extraction
from app.main import app
from app.routes.documents import get_blob_store
from app.storage.blobs import LocalBlobStore
from tests.docs import ANGLE_NDA, NDA, add_parsed_doc
from tests.fakes import FakeExtractor

pytestmark = pytest.mark.db


@pytest.fixture
def client(db, tmp_path):
    app.dependency_overrides[get_blob_store] = lambda: LocalBlobStore(tmp_path / "blobs")
    yield TestClient(app)
    app.dependency_overrides.clear()


def extract(entry):
    doc_id = add_parsed_doc(entry)
    settings = Settings(
        _env_file=None,
        anthropic_model_classify="claude-haiku-5-5",
        anthropic_model_extract="claude-sonnet-5-5",
    )
    run_extraction(doc_id, RunContext(FakeExtractor(entry), SpendGuard("1"), settings, actor="t"))
    return doc_id


def test_queue_filters_by_status_and_shows_flags(client):
    review, clean = extract(ANGLE_NDA), extract(NDA)

    everything = {d["id"]: d for d in client.get("/review/documents").json()}
    only_review = client.get("/review/documents", params={"status": "needs_review"}).json()

    assert set(everything) == {str(review), str(clean)}
    assert [d["id"] for d in only_review] == [str(review)]
    assert only_review[0]["flags"] == ["playbook_deviation"]
    assert only_review[0]["deviations"] == 1


def test_detail_has_fields_quotes_playbook_and_audit(client):
    doc_id = extract(ANGLE_NDA)

    body = client.get(f"/review/documents/{doc_id}").json()

    fields = {f["name"]: f for f in body["fields"]}
    assert fields["governing_law"]["quote"] and fields["governing_law"]["page"]
    assert fields["governing_law"]["boxes"]  # the evidence blocks, for highlighting
    deviation = next(r for r in body["playbook_results"] if r["outcome"] == "deviation")
    assert deviation["rule_id"] == "no_residuals" and deviation["reason"]
    assert [a["to_status"] for a in body["audit"]][-2:] == ["extracted", "needs_review"]


def test_approve_moves_to_approved_and_is_audited(client):
    doc_id = extract(ANGLE_NDA)

    assert client.post(f"/review/documents/{doc_id}/approve").json()["status"] == "approved"

    audit = client.get(f"/review/documents/{doc_id}").json()["audit"]
    assert (audit[-1]["from_status"], audit[-1]["to_status"], audit[-1]["actor"]) == (
        "needs_review",
        "approved",
        "reviewer",
    )
    assert client.post(f"/review/documents/{doc_id}/approve").status_code == 409


def test_override_is_logged_and_rechecks_the_playbook(client):
    doc_id = extract(ANGLE_NDA)

    resp = client.post(
        f"/review/documents/{doc_id}/fields/residuals_clause/override",
        json={"value": None, "note": "clause struck in the redline"},
    )

    body = resp.json()
    field = next(f for f in body["fields"] if f["name"] == "residuals_clause")
    assert field["overridden"] and field["value"] is None and field["original_value"] is True
    assert {r["outcome"] for r in body["playbook_results"]} == {"pass"}
    assert body["status"] == "needs_review"  # only a person's approve moves it on
    last = body["audit"][-1]
    assert last["detail"]["event"] == "override" and last["detail"]["old"] is True
    assert last["detail"]["note"] == "clause struck in the redline"


def test_override_unknown_field_is_404_and_missing_document_is_404(client):
    doc_id = extract(ANGLE_NDA)
    import uuid

    assert (
        client.post(f"/review/documents/{doc_id}/fields/nope/override", json={}).status_code == 404
    )
    assert client.get(f"/review/documents/{uuid.uuid4()}").status_code == 404
