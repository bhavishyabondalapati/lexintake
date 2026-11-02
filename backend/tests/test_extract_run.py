"""The extraction step end to end, with the fake extractor (no API calls) and a real database."""

from decimal import Decimal

import pytest
from sqlalchemy import select

from app.config import Settings
from app.db import _lock_key, get_engine, session_scope
from app.extraction.base import LlmCallFailed
from app.extraction.pricing import SpendGuard
from app.extraction.run import RetryableExtractionError, RunContext, run_extraction
from app.models import AuditLog, Document, Extraction, LlmCall, Status
from tests.docs import LEGAL_NOTICE, NDA, SIDE_LETTER, add_parsed_doc
from tests.fakes import FakeExtractor

pytestmark = pytest.mark.db

CHEAP, STRONG = "claude-haiku-5-5", "claude-sonnet-5-5"


def _settings(**overrides) -> Settings:
    values = {"anthropic_model_classify": CHEAP, "anthropic_model_extract": STRONG} | overrides
    return Settings(_env_file=None, **values)


def _run(doc_id, extractor, limit="1.00", force=False, settings=None):
    ctx = RunContext(extractor, SpendGuard(limit), settings or _settings(), actor="test")
    return run_extraction(doc_id, ctx, force=force)


def _state(doc_id):
    with session_scope() as session:
        doc = session.get(Document, doc_id)
        extraction = session.scalars(
            select(Extraction).where(Extraction.document_id == doc_id)
        ).first()
        calls = session.scalars(
            select(LlmCall).where(LlmCall.document_id == doc_id).order_by(LlmCall.id)
        ).all()
        audit = session.scalars(
            select(AuditLog).where(AuditLog.document_id == doc_id).order_by(AuditLog.id)
        ).all()
        return doc, extraction, calls, [(a.from_status, a.to_status) for a in audit]


def _codes(extraction):
    return {r["code"] for r in extraction.review_reasons}


def test_nda_is_classified_then_extracted_with_the_strong_model(db):
    doc_id = add_parsed_doc(NDA)
    fake = FakeExtractor(NDA)

    outcome = _run(doc_id, fake)

    doc, extraction, calls, audit = _state(doc_id)
    assert outcome.result == "extracted", extraction.review_reasons
    assert doc.status == Status.EXTRACTED and doc.doc_type == "nda"
    assert audit[-2:] == [("parsed", "extracting"), ("extracting", "extracted")]
    assert [(c.step, c.model) for c in calls] == [("classify", CHEAP), ("extract", STRONG)]
    assert all(c.cost_usd > 0 for c in calls)
    assert outcome.cost_usd == sum(c.cost_usd for c in calls)
    for name, value in NDA["fields"].items():
        expected = value if value is not None else None
        assert extraction.fields[name]["value"] == expected, name
        assert extraction.fields[name]["status"] == ("absent" if value is None else "verified")
    assert extraction.classification["status"] == "verified"


def test_other_types_use_the_cheap_model_for_their_small_field_set(db):
    doc_id = add_parsed_doc(LEGAL_NOTICE)

    outcome = _run(doc_id, FakeExtractor(LEGAL_NOTICE))

    doc, extraction, calls, _ = _state(doc_id)
    assert outcome.result == "extracted", extraction.review_reasons
    assert [(c.step, c.model) for c in calls] == [("classify", CHEAP), ("extract", CHEAP)]
    assert set(extraction.fields) == set(LEGAL_NOTICE["fields"])
    assert doc.doc_subtype == "notice_of_breach"


def test_other_is_out_of_scope_and_goes_to_review(db):
    doc_id = add_parsed_doc(LEGAL_NOTICE)
    fake = FakeExtractor(
        LEGAL_NOTICE, classification={"doc_type": "other", "subtype": "newsletter"}
    )

    outcome = _run(doc_id, fake)

    assert outcome.result == "needs_review"
    assert "out_of_scope" in _codes(_state(doc_id)[1])


def test_invalid_output_is_retried_once(db):
    doc_id = add_parsed_doc(NDA)

    outcome = _run(doc_id, FakeExtractor(NDA, invalid_times=1))

    _, _, calls, _ = _state(doc_id)
    assert outcome.result == "extracted"
    assert [(c.step, c.attempt, c.outcome) for c in calls] == [
        ("classify", 1, "invalid_output"),
        ("classify", 2, "ok"),
        ("extract", 1, "ok"),
    ]
    assert calls[0].cost_usd > 0  # a failed attempt still costs money and is recorded


def test_invalid_output_twice_goes_to_review_without_extracting(db):
    doc_id = add_parsed_doc(NDA)

    outcome = _run(doc_id, FakeExtractor(NDA, invalid_times=2))

    doc, extraction, calls, _ = _state(doc_id)
    assert outcome.result == "needs_review"
    assert _codes(extraction) == {"invalid_output"}
    assert len(calls) == 2 and doc.doc_type is None


def test_a_refusal_goes_to_review(db):
    doc_id = add_parsed_doc(NDA)

    assert _run(doc_id, FakeExtractor(NDA, refuse=True)).result == "needs_review"
    _, extraction, calls, _ = _state(doc_id)
    assert _codes(extraction) == {"refused"} and len(calls) == 1  # not retried


def test_retryable_api_error_is_recorded_and_raised_for_the_caller_to_retry(db):
    doc_id = add_parsed_doc(NDA)
    fake = FakeExtractor(NDA, error=LlmCallFailed("RateLimitError 429", retryable=True))

    with pytest.raises(RetryableExtractionError):
        _run(doc_id, fake)

    doc, _, calls, _ = _state(doc_id)
    assert doc.status == Status.EXTRACTING  # the worker retries; a redelivery is allowed
    assert [(c.outcome, c.cost_usd) for c in calls] == [("api_error", Decimal(0))]
    assert _run(doc_id, FakeExtractor(NDA)).result == "extracted"  # the retry works


def test_non_retryable_api_error_fails_the_document(db):
    doc_id = add_parsed_doc(NDA)
    fake = FakeExtractor(NDA, error=LlmCallFailed("BadRequestError 400: bad", retryable=False))

    assert _run(doc_id, fake).result == "failed"
    doc, _, _, audit = _state(doc_id)
    assert doc.status == Status.FAILED and "400" in doc.error
    assert audit[-1] == ("extracting", "failed")
    # A failure during extraction can be re-run as an extraction (not a re-parse).
    assert _run(doc_id, FakeExtractor(NDA)).result == "extracted"
    assert _state(doc_id)[0].error is None


def test_invented_quotes_never_pass(db):
    doc_id = add_parsed_doc(NDA)

    outcome = _run(doc_id, FakeExtractor(NDA, invent_quotes=True))

    _, extraction, calls, _ = _state(doc_id)
    assert outcome.result == "needs_review"
    assert "classification_unverified" in _codes(extraction)
    assert len(calls) == 1  # the type is in doubt, so the strong model is never called


def test_low_classification_confidence_skips_extraction(db):
    doc_id = add_parsed_doc(NDA)

    outcome = _run(doc_id, FakeExtractor(NDA, classification={"confidence": 0.6}))

    _, extraction, calls, _ = _state(doc_id)
    assert outcome.result == "needs_review"
    assert _codes(extraction) == {"low_classification_confidence"} and len(calls) == 1


def test_confidence_alone_never_passes_a_failed_check(db):
    doc_id = add_parsed_doc(NDA)
    fake = FakeExtractor(
        NDA, fields={"governing_law": {"quote": "laws of Atlantis", "confidence": 1.0}}
    )

    assert _run(doc_id, fake).result == "needs_review"
    assert ("field_unverified", "governing_law") in {
        (r["code"], r["field"]) for r in _state(doc_id)[1].review_reasons
    }


def test_a_verified_field_with_low_model_confidence_goes_to_review(db):
    doc_id = add_parsed_doc(NDA)

    _run(doc_id, FakeExtractor(NDA, fields={"term_months": {"confidence": 0.5}}))

    assert "low_field_confidence" in _codes(_state(doc_id)[1])


def test_ocr_fields_need_more_confidence(db):
    doc_id = add_parsed_doc(SIDE_LETTER, source="ocr")

    _run(doc_id, FakeExtractor(SIDE_LETTER, fields={"investor": {"confidence": 0.85}}))

    reasons = _state(doc_id)[1].review_reasons
    assert {
        "code": "low_field_confidence",
        "field": "investor",
        "detail": "0.85 < 0.9 (OCR)",
    } in reasons


def test_nda_guard_catches_an_nda_classified_as_something_else(db):
    doc_id = add_parsed_doc(NDA)
    fake = FakeExtractor(NDA, classification={"doc_type": "side_letter", "subtype": None})

    outcome = _run(doc_id, fake)

    _, extraction, calls, _ = _state(doc_id)
    assert outcome.result == "needs_review"
    assert "nda_signals_but_not_nda" in _codes(extraction)
    assert len(calls) == 1


def test_absent_clause_mentioned_in_text_goes_to_review(db):
    entry = NDA | {"fields": NDA["fields"] | {"non_compete_months": None}}
    doc_id = add_parsed_doc(
        entry, extra=["Recipient shall not engage in any business that competes with the Company."]
    )

    _run(doc_id, FakeExtractor(entry))

    reasons = _state(doc_id)[1].review_reasons
    assert any(
        r["code"] == "absent_but_mentioned" and r["field"] == "non_compete_months" for r in reasons
    )


def test_partial_parse_goes_to_review(db):
    doc_id = add_parsed_doc(NDA, partial=True)

    _run(doc_id, FakeExtractor(NDA))

    assert "partial_parse" in _codes(_state(doc_id)[1])


def test_done_documents_are_skipped_unless_forced(db):
    doc_id = add_parsed_doc(NDA)
    _run(doc_id, FakeExtractor(NDA))

    assert _run(doc_id, FakeExtractor(NDA)).result == "already_done"
    assert _run(doc_id, FakeExtractor(NDA), force=True).result == "extracted"

    with session_scope() as session:
        assert len(session.scalars(select(Extraction)).all()) == 1  # replaced, not appended
    assert len(_state(doc_id)[2]) == 4


def test_spend_limit_stops_before_any_call(db):
    doc_id = add_parsed_doc(NDA)
    fake = FakeExtractor(NDA)

    outcome = _run(doc_id, fake, limit="0.0001")

    doc, extraction, calls, audit = _state(doc_id)
    assert outcome.result == "spend_limit"
    assert fake.calls == [] and calls == [] and extraction is None
    assert doc.status == Status.PARSED and audit[-1] == ("extracting", "parsed")


def test_spend_limit_can_stop_between_classify_and_extract(db):
    doc_id = add_parsed_doc(NDA)
    fake = FakeExtractor(NDA)

    # Enough for a cheap classify call's worst case, not for the strong model's.
    outcome = _run(doc_id, fake, limit="0.01")

    assert outcome.result == "spend_limit"
    assert [p.step for p in fake.calls] == ["classify"]
    assert _state(doc_id)[0].status == Status.PARSED


def test_missing_model_setting_fails_with_a_clear_message(db):
    doc_id = add_parsed_doc(NDA)

    outcome = _run(doc_id, FakeExtractor(NDA), settings=_settings(anthropic_model_extract=""))

    assert outcome.result == "failed"
    assert "ANTHROPIC_MODEL_EXTRACT is not set" in _state(doc_id)[0].error


def test_a_model_without_a_price_refuses_to_run(db):
    doc_id = add_parsed_doc(NDA)

    outcome = _run(
        doc_id, FakeExtractor(NDA), settings=_settings(anthropic_model_classify="mystery")
    )

    assert outcome.result == "failed"
    assert "no price for model 'mystery'" in _state(doc_id)[0].error


def test_document_locked_by_another_worker_is_skipped(db):
    doc_id = add_parsed_doc(NDA)
    with get_engine().connect() as other_worker:
        from sqlalchemy import func

        other_worker.scalar(select(func.pg_advisory_lock(_lock_key(doc_id))))
        assert _run(doc_id, FakeExtractor(NDA)).result == "locked"
        other_worker.scalar(select(func.pg_advisory_unlock(_lock_key(doc_id))))


def test_a_document_that_is_not_parsed_yet_is_left_alone(db):
    doc_id = add_parsed_doc(NDA, status=Status.PARSING)

    assert _run(doc_id, FakeExtractor(NDA)).result == "not_parsed"
