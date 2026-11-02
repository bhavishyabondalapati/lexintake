"""The extract step as a Celery task (queue `extract`, run by the `extractor` service).

Only used when EXTRACT_ON_PARSE=true or when something queues it explicitly; by default
`python -m app.cli extract` runs the same code in the foreground. The spend cap applies per UTC
day here, counted from llm_calls.
"""

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from functools import lru_cache

from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError

from app.config import get_settings
from app.db import session_scope
from app.extraction.anthropic_extractor import AnthropicExtractor
from app.extraction.pricing import SpendGuard
from app.extraction.run import RetryableExtractionError, RunContext, mark_failed, run_extraction
from app.models import LlmCall
from app.worker import EXTRACT_TASK, celery_app

MAX_RETRIES = 3


@lru_cache
def get_extractor() -> AnthropicExtractor:
    return AnthropicExtractor(get_settings())


def spent_today() -> Decimal:
    midnight = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    with session_scope() as session:
        total = session.scalar(
            select(func.coalesce(func.sum(LlmCall.cost_usd), 0)).where(
                LlmCall.created_at >= midnight
            )
        )
    return Decimal(total)


@celery_app.task(
    bind=True,
    name=EXTRACT_TASK,
    autoretry_for=(OperationalError,),
    retry_backoff=True,
    max_retries=MAX_RETRIES,
)
def extract_document(self, document_id: str) -> str:
    settings = get_settings()
    doc_id = uuid.UUID(document_id)
    ctx = RunContext(
        extractor=get_extractor(),
        guard=SpendGuard(settings.max_spend_usd, already_spent=spent_today()),
        settings=settings,
    )
    try:
        return run_extraction(doc_id, ctx).result
    except RetryableExtractionError as e:
        # The SDK already retried a few times within seconds; now wait longer between tries.
        if self.request.retries >= MAX_RETRIES:
            mark_failed(doc_id, f"gave up after {self.request.retries + 1} tries: {e}", ctx.actor)
            return "failed"
        raise self.retry(exc=e, countdown=min(300, 30 * 2**self.request.retries)) from e
