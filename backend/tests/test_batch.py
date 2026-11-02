"""Batch mode against a stub Batch API (nothing is sent). The stub answers with the fake
extractor's output, so the checks and routing run for real."""

import json
import uuid
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.cli import truncated_count
from app.config import Settings
from app.db import session_scope
from app.extraction.batch import BatchRunner, split_custom_id
from app.extraction.doctext import load_doc_text
from app.extraction.pricing import cost_usd
from app.models import Document, LlmCall, Status
from tests.docs import NDA, SIDE_LETTER, add_parsed_doc
from tests.fakes import FakeExtractor

pytestmark = pytest.mark.db

SETTINGS = Settings(
    _env_file=None,
    anthropic_model_classify="claude-haiku-5-5",
    anthropic_model_extract="claude-sonnet-5-5",
)
ENTRIES = {NDA["doc_id"]: NDA, SIDE_LETTER["doc_id"]: SIDE_LETTER}


class StubBatches:
    """Remembers what was submitted; answers each request the way the fake extractor would."""

    def __init__(self, entry_for, reject_grammar=False, errors=(), truncate=()):
        self.entry_for, self.reject_grammar, self.errors = entry_for, reject_grammar, set(errors)
        self.truncate = set(truncate)
        self.created: list[list[dict]] = []
        self.polls = 0

    def create(self, requests):
        self.created.append(requests)
        return SimpleNamespace(id=f"batch_{len(self.created)}")

    def retrieve(self, batch_id):
        self.polls += 1
        return SimpleNamespace(processing_status="ended" if self.polls % 2 == 0 else "in_progress")

    def results(self, batch_id):
        number = int(batch_id.split("_")[1])
        for request in self.created[number - 1]:
            yield self._entry(request)

    def _entry(self, request):
        cid, params = request["custom_id"], request["params"]
        document_id, step = split_custom_id(cid)
        strict = "format" in params["output_config"]
        if cid in self.errors:
            result = {"type": "errored", "error": {"type": "error", "error": {"message": "boom"}}}
        elif self.reject_grammar and strict and step == "extract":
            result = {
                "type": "errored",
                "error": {"error": {"message": "The compiled grammar is too large"}},
            }
        else:
            message = self._message(document_id, step)
            if cid in self.truncate:  # the model ran out of tokens mid-JSON
                text = message["content"][0]["text"]
                message |= {
                    "stop_reason": "max_tokens",
                    "content": [{"type": "text", "text": text[:40]}],
                }
            result = {"type": "succeeded", "message": message}
        return SimpleNamespace(custom_id=cid, model_dump=lambda mode=None: {"result": result})

    def _message(self, document_id, step):
        from app.extraction.base import plan_classify, plan_extract
        from app.extraction.schemas import DocType

        entry = self.entry_for(document_id)
        with session_scope() as session:
            doc = load_doc_text(session, document_id)
        plan = (
            plan_classify(SETTINGS, doc)
            if step == "classify"
            else plan_extract(SETTINGS, doc, DocType(entry["doc_type"]), None)
        )
        fake = FakeExtractor(entry).call(plan, doc)
        return {
            "id": "msg_x",
            "type": "message",
            "role": "assistant",
            "model": plan.model,
            "stop_reason": "end_turn",
            "stop_sequence": None,
            "content": [{"type": "text", "text": fake.raw_text}],
            "usage": {"input_tokens": 1000, "output_tokens": 200},
        }


def runner(tmp_path, stub):
    client = SimpleNamespace(messages=SimpleNamespace(batches=stub))
    return BatchRunner(client, SETTINGS, tmp_path / "batch", sleep=lambda s: None)


def prepare():
    ids = {name: add_parsed_doc(entry) for name, entry in ENTRIES.items()}
    by_id = {ids[name]: entry for name, entry in ENTRIES.items()}
    return ids, by_id


def test_batch_routes_like_the_live_path_and_records_half_price(db, tmp_path):
    ids, by_id = prepare()
    stub = StubBatches(by_id.get)

    outcomes = runner(tmp_path, stub).run(list(ids.values()), Decimal("5"))

    assert outcomes[ids["syn_00010"]].result == "auto_approved"
    assert len(stub.created) == 2  # one classify batch, one extract batch
    assert {r["custom_id"].rsplit("_", 1)[1] for r in stub.created[1]} == {"extract"}
    with session_scope() as session:
        calls = session.scalars(select(LlmCall)).all()
        assert len(calls) == 4
        sonnet = next(c for c in calls if c.model == "claude-sonnet-5-5")
        full = cost_usd("claude-sonnet-5-5", 1000, 200)  # list price
        assert sonnet.cost_usd == full / 2


def test_a_spend_cap_below_the_worst_case_sends_nothing(db, tmp_path):
    ids, by_id = prepare()
    stub = StubBatches(by_id.get)

    with pytest.raises(SystemExit):
        runner(tmp_path, stub).run(list(ids.values()), Decimal("0.0001"))

    assert stub.created == []


def test_a_request_the_api_rejects_for_grammar_size_is_resubmitted_without_it(db, tmp_path):
    ids, by_id = prepare()
    stub = StubBatches(by_id.get, reject_grammar=True)

    outcomes = runner(tmp_path, stub).run(list(ids.values()), Decimal("5"))

    assert len(stub.created) == 3  # classify, extract (rejected), extract again without grammar
    assert all("format" not in r["params"]["output_config"] for r in stub.created[2])
    assert outcomes[ids["syn_00010"]].result == "auto_approved"


def test_a_failed_request_fails_that_document_only(db, tmp_path):
    ids, by_id = prepare()
    bad = f"{ids['syn_00010']}_extract"
    stub = StubBatches(by_id.get, errors=[bad])

    outcomes = runner(tmp_path, stub).run(list(ids.values()), Decimal("5"))

    assert outcomes[ids["syn_00010"]].result == "failed"
    assert outcomes[ids["syn_00002"]].result in ("extracted", "needs_review")
    with session_scope() as session:
        doc = session.get(Document, ids["syn_00010"])
        assert doc.status == Status.FAILED and "boom" in doc.error


def test_a_stopped_run_resumes_without_submitting_again(db, tmp_path):
    ids, by_id = prepare()
    first = StubBatches(by_id.get)
    runner(tmp_path, first).run(list(ids.values()), Decimal("5"))
    assert len(first.created) == 2

    again = StubBatches(by_id.get)
    outcomes = runner(tmp_path, again).run(list(ids.values()), Decimal("5"), force=True)

    assert again.created == []  # batch ids and answers were on disk
    assert outcomes[ids["syn_00010"]].result == "auto_approved"
    saved = json.loads((tmp_path / "batch" / "state.json").read_text())
    assert saved["classify"] == "batch_1"


def test_batch_requests_are_bounded_and_a_truncated_answer_goes_to_review(db, tmp_path):
    ids, by_id = prepare()
    nda = ids["syn_00010"]
    stub = StubBatches(by_id.get, truncate=[f"{nda}_extract"])
    run_id = uuid.uuid4()

    outcomes = runner(tmp_path, stub).run(list(ids.values()), Decimal("5"), run_id)

    classify, extract = stub.created
    assert {r["params"]["max_tokens"] for r in classify} == {400}
    sent = {r["custom_id"]: r["params"]["max_tokens"] for r in extract}
    assert sent[f"{nda}_extract"] == 1_500  # NDA fields
    assert sent[f"{ids['syn_00002']}_extract"] == 1_200  # the small field set of another type

    assert outcomes[nda].result == "needs_review"
    assert [r.code for r in outcomes[nda].reasons] == ["truncated"]
    assert truncated_count(run_id) == 1  # what the CLI report prints
