"""The Celery extract task: daily spend cap and retries (fake extractor, run eagerly)."""

from decimal import Decimal

import pytest

from app.config import get_settings
from app.db import session_scope
from app.extraction.base import LlmCallFailed
from app.models import Document, LlmCall, Status
from app.tasks import extract as task
from tests.docs import NDA, add_parsed_doc
from tests.fakes import FakeExtractor

pytestmark = pytest.mark.db


@pytest.fixture
def worker_env(db, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_MODEL_CLASSIFY", "claude-haiku-5-5")
    monkeypatch.setenv("ANTHROPIC_MODEL_EXTRACT", "claude-sonnet-5-5")
    get_settings.cache_clear()
    yield monkeypatch
    get_settings.cache_clear()


def _status(doc_id):
    with session_scope() as session:
        return session.get_one(Document, doc_id).status


def test_task_extracts_a_parsed_document(worker_env):
    doc_id = add_parsed_doc(NDA)
    worker_env.setattr(task, "get_extractor", lambda: FakeExtractor(NDA))

    assert task.extract_document.apply(args=[str(doc_id)]).get() == "auto_approved"
    assert _status(doc_id) == Status.AUTO_APPROVED


def test_daily_spend_cap_counts_what_was_already_spent_today(worker_env):
    done, waiting = add_parsed_doc(NDA), add_parsed_doc(NDA)
    with session_scope() as session:
        session.add(
            LlmCall(
                document_id=done,
                step="extract",
                attempt=1,
                model="claude-sonnet-5-5",
                cost_usd=Decimal("0.99"),
                outcome="ok",
            )
        )
    worker_env.setenv("MAX_SPEND_USD", "1.00")
    get_settings.cache_clear()
    worker_env.setattr(task, "get_extractor", lambda: FakeExtractor(NDA))

    assert task.spent_today() == Decimal("0.99")
    assert task.extract_document.apply(args=[str(waiting)]).get() == "spend_limit"
    assert _status(waiting) == Status.PARSED


def test_task_retries_api_errors_then_marks_the_document_failed(worker_env):
    doc_id = add_parsed_doc(NDA)
    fake = FakeExtractor(NDA, error=LlmCallFailed("OverloadedError 529", retryable=True))
    worker_env.setattr(task, "get_extractor", lambda: fake)

    assert task.extract_document.apply(args=[str(doc_id)]).get() == "failed"

    assert len(fake.calls) == task.MAX_RETRIES + 1
    assert _status(doc_id) == Status.FAILED
