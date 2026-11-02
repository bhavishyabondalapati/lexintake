"""The extraction step for one document: classify -> extract -> verify -> route.

Safe to run twice, or at the same time, on the same document:
- a document that is already extracted / needs_review is left alone (unless `force`);
- the advisory lock shared with parsing lets only one worker touch a document at once;
- the extractions row is replaced in the same transaction that sets the final status.

Every API call is saved to llm_calls in its own transaction as soon as it returns, so the money
spent is on record even if the step crashes afterwards.
"""

import logging
import uuid
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import delete, select

from app.config import Settings
from app.db import document_lock, session_scope
from app.extraction.base import (
    CallPlan,
    Extractor,
    LlmCallFailed,
    LlmResult,
    ModelNotConfigured,
    plan_classify,
    plan_extract,
)
from app.extraction.doctext import DocText, load_doc_text
from app.extraction.pricing import SpendGuard, SpendLimitReached, UnknownModelPrice, cost_usd
from app.extraction.prompts import PROMPT_VERSION
from app.extraction.route import (
    Reason,
    classification_reasons,
    document_reasons,
    field_reasons,
    playbook_reasons,
    scope_reasons,
)
from app.extraction.schemas import SCHEMA_VERSION, CitedFields, Classification, DocType
from app.extraction.verify import (
    check_classification,
    check_fields,
    injection_signals,
    nda_signals,
)
from app.models import AuditLog, Document, Extraction, LlmCall, LlmOutcome, Status
from app.playbook import check_playbook, load_playbook
from app.status import transition

log = logging.getLogger(__name__)

MAX_OUTPUT_ATTEMPTS = 2  # invalid output: retry once, then needs_review
MAX_ERROR_CHARS = 2000
RERUNNABLE = {Status.EXTRACTED, Status.NEEDS_REVIEW, Status.AUTO_APPROVED}


@dataclass
class RunContext:
    extractor: Extractor
    guard: SpendGuard
    settings: Settings
    run_id: uuid.UUID | None = None
    actor: str = "worker:extract"


@dataclass
class Outcome:
    result: str  # extracted | needs_review | spend_limit | already_done | locked | ...
    cost_usd: Decimal = Decimal(0)
    reasons: list[Reason] = field(default_factory=list)


class RetryableExtractionError(Exception):
    """The API kept failing in a way that may pass (rate limit, overload, network). The caller
    decides: the worker retries later, the CLI marks the document failed."""


def failed_during_extraction(session, doc: Document) -> bool:
    last = session.scalars(
        select(AuditLog)
        .where(AuditLog.document_id == doc.id, AuditLog.to_status == Status.FAILED)
        .order_by(AuditLog.id.desc())
        .limit(1)
    ).first()
    return last is not None and last.from_status == Status.EXTRACTING


def run_extraction(document_id: uuid.UUID, ctx: RunContext, force: bool = False) -> Outcome:
    with document_lock(document_id) as got_lock:
        if not got_lock:
            return Outcome("locked")
        return _run_locked(document_id, ctx, force)


def _run_locked(document_id: uuid.UUID, ctx: RunContext, force: bool) -> Outcome:
    # Step 1: claim the document.
    with session_scope() as session:
        doc = session.get(Document, document_id, with_for_update=True)
        if doc is None:
            return Outcome("missing")
        status = Status(doc.status)
        if status in RERUNNABLE and not force:
            return Outcome("already_done")
        if status == Status.FAILED and not failed_during_extraction(session, doc):
            return Outcome("not_parsed")  # it failed while parsing: requeue the parse instead
        if status not in RERUNNABLE | {Status.PARSED, Status.EXTRACTING, Status.FAILED}:
            return Outcome("not_parsed")
        transition(
            session,
            doc,
            Status.EXTRACTING,
            ctx.actor,
            {"run_id": str(ctx.run_id) if ctx.run_id else None, "forced": force},
        )
        doc_text = load_doc_text(session, document_id)

    # Step 2: the API calls, outside any transaction.
    step = _Step(document_id, doc_text, ctx)
    try:
        reasons, extraction = step.run()
    except SpendLimitReached as e:
        _finish(document_id, Status.PARSED, ctx, {"event": "spend_limit", "detail": str(e)})
        return Outcome("spend_limit", step.cost)
    except (ModelNotConfigured, UnknownModelPrice) as e:
        mark_failed(document_id, f"{type(e).__name__}: {e}", ctx.actor)
        return Outcome("failed", step.cost)
    except LlmCallFailed as e:
        if e.retryable:
            raise RetryableExtractionError(str(e)) from e
        mark_failed(document_id, f"API error: {e}", ctx.actor)
        return Outcome("failed", step.cost)

    # Step 3: save the result and route, all or nothing.
    # NDAs also go through the playbook (their fields exist only if the type was not in doubt).
    is_nda = extraction.doc_type == DocType.NDA.value and bool(extraction.fields)
    if is_nda:
        results = check_playbook(load_playbook(), extraction.fields)
        extraction.playbook_results = [r.to_json() for r in results]
        pb_reasons = playbook_reasons(results)
        reasons = reasons + pb_reasons
    else:
        pb_reasons = []
    extraction.review_reasons = [r.to_json() for r in reasons]
    detail = {
        "run_id": str(ctx.run_id) if ctx.run_id else None,
        "doc_type": extraction.doc_type,
        "reasons": sorted({r.code for r in reasons}),
        "cost_usd": str(step.cost),
    }
    if reasons and len(reasons) > len(pb_reasons):
        final, then = Status.NEEDS_REVIEW, None  # an extraction check failed already
    elif is_nda:
        # extracted, then the playbook decides
        final, then = Status.EXTRACTED, Status.NEEDS_REVIEW if reasons else Status.AUTO_APPROVED
    else:
        final, then = Status.EXTRACTED, None
    _finish(document_id, final, ctx, detail, extraction, then)
    return Outcome((then or final).value, step.cost, reasons)


class _Step:
    def __init__(self, document_id: uuid.UUID, doc: DocText, ctx: RunContext) -> None:
        self.document_id, self.doc, self.ctx = document_id, doc, ctx
        self.cost = Decimal(0)

    def run(self) -> tuple[list[Reason], Extraction]:
        settings, doc = self.ctx.settings, self.doc
        injection = injection_signals(doc)
        reasons = document_reasons(doc, injection)
        extraction = Extraction(
            document_id=self.document_id,
            classification={},
            fields={},
            prompt_version=PROMPT_VERSION,
            schema_version=SCHEMA_VERSION,
        )

        # Classify (cheap model).
        result = self._call(plan_classify(settings, doc))
        if result.outcome != LlmOutcome.OK:
            extraction.classification = {"error": result.error, "outcome": result.outcome}
            reason = Reason(result.outcome.value, "classification", result.error or "")
            return [*reasons, reason], extraction
        c = result.output
        assert isinstance(c, Classification)
        check = check_classification(doc, c)
        signals = nda_signals(doc)
        extraction.doc_type = c.doc_type.value
        extraction.doc_subtype = c.subtype.value if c.subtype else None
        extraction.classification = {
            "doc_type": c.doc_type.value,
            "subtype": extraction.doc_subtype,
            **check.to_json(),
            "nda_signals": signals,
            "injection_signals": injection,
        }
        doubts = classification_reasons(settings, c, check, signals)
        if doubts:
            return reasons + doubts, extraction  # the type is in doubt: don't pay for step 2
        reasons += scope_reasons(c)

        # Extract (NDA: strong model; everything else: the cheap model, small field set).
        result = self._call(plan_extract(settings, doc, c.doc_type, c.subtype))
        if result.outcome != LlmOutcome.OK:
            return [*reasons, Reason(result.outcome.value, None, result.error or "")], extraction
        assert isinstance(result.output, CitedFields)
        checks = check_fields(doc, result.output, c.doc_type)
        extraction.fields = {check.name: check.to_json() for check in checks}
        return reasons + field_reasons(settings, checks), extraction

    def _call(self, plan: CallPlan) -> LlmResult:
        """One step's call: at most one retry when the output is unusable."""
        result = None
        for attempt in range(1, MAX_OUTPUT_ATTEMPTS + 1):
            self.ctx.guard.check(plan.worst_case_usd)
            try:
                result = self.ctx.extractor.call(plan, self.doc)
            except LlmCallFailed as e:
                self._record(plan, attempt, None, error=str(e))
                raise
            self._record(plan, attempt, result)
            if result.outcome != LlmOutcome.INVALID_OUTPUT:
                break
            log.warning(
                "%s: invalid %s output (attempt %d): %s",
                self.document_id,
                plan.step,
                attempt,
                result.error,
            )
        assert result is not None
        return result

    def _record(self, plan: CallPlan, attempt: int, result: LlmResult | None, error=None) -> None:
        model = result.model if result else plan.model
        usage = result.usage if result else None
        cost = (
            cost_usd(
                model,
                usage.input_tokens,
                usage.output_tokens,
                usage.cache_write_tokens,
                usage.cache_read_tokens,
            )
            if usage
            else Decimal(0)
        )
        self.cost += cost
        self.ctx.guard.add(cost)
        err = result.error if result else error
        with session_scope() as session:
            session.add(
                LlmCall(
                    document_id=self.document_id,
                    run_id=self.ctx.run_id,
                    step=plan.step,
                    attempt=attempt,
                    model=model,
                    input_tokens=usage.input_tokens if usage else 0,
                    output_tokens=usage.output_tokens if usage else 0,
                    cache_write_tokens=usage.cache_write_tokens if usage else 0,
                    cache_read_tokens=usage.cache_read_tokens if usage else 0,
                    cost_usd=cost,
                    stop_reason=result.stop_reason if result else None,
                    outcome=result.outcome if result else LlmOutcome.API_ERROR,
                    error=err[:MAX_ERROR_CHARS] if err else None,
                    request_id=result.request_id if result else None,
                    raw_output=result.raw_text if result else None,
                    latency_ms=result.latency_ms if result else 0,
                )
            )


def _finish(
    document_id: uuid.UUID,
    to: Status,
    ctx: RunContext,
    detail: dict,
    extraction: Extraction | None = None,
    then: Status | None = None,
) -> None:
    """Set the status (and save the extraction) in one transaction. `then` is a second hop, e.g.
    extracted -> auto_approved; both hops get an audit row."""
    with session_scope() as session:
        doc = session.get_one(Document, document_id, with_for_update=True)
        if extraction is not None:
            session.execute(delete(Extraction).where(Extraction.document_id == document_id))
            session.add(extraction)
            doc.doc_type, doc.doc_subtype = extraction.doc_type, extraction.doc_subtype
            if doc.error and not doc.error.startswith("partial"):
                doc.error = None  # an earlier failed attempt; this one got through
        transition(session, doc, to, ctx.actor, detail)
        if then is not None:
            transition(session, doc, then, "playbook", detail)


def mark_failed(document_id: uuid.UUID, error: str, actor: str) -> None:
    with session_scope() as session:
        doc = session.get_one(Document, document_id, with_for_update=True)
        doc.error = error[:MAX_ERROR_CHARS]
        transition(session, doc, Status.FAILED, actor, {"error": doc.error})
