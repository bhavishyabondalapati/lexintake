"""Small admin commands.

python -m app.cli requeue                  # re-queue documents stuck in `received`
python -m app.cli requeue --status failed  # retry failed ones (the step that failed)
python -m app.cli extract --limit 20       # classify + extract parsed documents (calls the API)
python -m app.cli extract --limit 20 --max-spend 0.50 --force --doc <uuid>
"""

import argparse
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from sqlalchemy import func, select

from app.config import Settings, get_settings
from app.db import session_scope
from app.extraction.base import Extractor
from app.extraction.pricing import SpendGuard
from app.extraction.run import (
    RetryableExtractionError,
    RunContext,
    failed_during_extraction,
    mark_failed,
    run_extraction,
)
from app.models import Document, LlmCall, Status
from app.queue import enqueue_extract, enqueue_parse

ACTOR = "cli:extract"


def requeue(statuses: list[str]) -> int:
    parse_ids, extract_ids = [], []
    with session_scope() as session:
        docs = session.scalars(select(Document).where(Document.status.in_(statuses))).all()
        for doc in docs:
            if doc.status == Status.FAILED and failed_during_extraction(session, doc):
                extract_ids.append(doc.id)
                continue
            if doc.status == Status.FAILED:
                doc.retry_count = 0  # a manual retry gets a fresh set of attempts
            parse_ids.append(doc.id)
    for document_id in parse_ids:
        enqueue_parse(document_id)
    for document_id in extract_ids:
        enqueue_extract(document_id)
    return len(parse_ids) + len(extract_ids)


@dataclass
class ExtractRun:
    run_id: uuid.UUID
    results: dict[uuid.UUID, str] = field(default_factory=dict)
    stopped_by_spend_limit: bool = False
    spent: Decimal = Decimal(0)


def select_documents(
    limit: int | None, doc_ids: Sequence[uuid.UUID] = (), force: bool = False
) -> list[uuid.UUID]:
    with session_scope() as session:
        query = select(Document.id).order_by(Document.created_at, Document.id)
        if doc_ids:
            query = query.where(Document.id.in_(doc_ids))
        else:
            statuses = [Status.PARSED] + (
                [Status.EXTRACTED, Status.NEEDS_REVIEW, Status.AUTO_APPROVED] if force else []
            )
            query = query.where(Document.status.in_(statuses))
        if limit:
            query = query.limit(limit)
        return list(session.scalars(query))


def extract(
    extractor: Extractor,
    settings: Settings,
    limit: int | None = None,
    max_spend: Decimal | float | None = None,
    doc_ids: Sequence[uuid.UUID] = (),
    force: bool = False,
) -> ExtractRun:
    """Run the extraction step on documents one by one, stopping at the spend limit."""
    run = ExtractRun(run_id=uuid.uuid4())
    guard = SpendGuard(settings.max_spend_usd if max_spend is None else max_spend)
    ctx = RunContext(extractor, guard, settings, run_id=run.run_id, actor=ACTOR)
    for document_id in select_documents(limit, doc_ids, force):
        try:
            result = run_extraction(document_id, ctx, force=force).result
        except RetryableExtractionError as e:
            # The SDK has already retried with backoff; in the foreground we don't wait longer.
            mark_failed(document_id, f"API error after retries: {e}", ACTOR)
            result = "failed"
        run.results[document_id] = result
        if result == "spend_limit":
            run.stopped_by_spend_limit = True
            break
    run.spent = guard.spent
    return run


def cost_table(run_id: uuid.UUID) -> list[dict]:
    """Per step and model: calls, tokens (including cache reads/writes) and cost."""
    with session_scope() as session:
        rows = session.execute(
            select(
                LlmCall.step,
                LlmCall.model,
                func.count(),
                func.count(func.distinct(LlmCall.document_id)),
                func.sum(LlmCall.input_tokens),
                func.sum(LlmCall.output_tokens),
                func.sum(LlmCall.cache_write_tokens),
                func.sum(LlmCall.cache_read_tokens),
                func.sum(LlmCall.cost_usd),
            )
            .where(LlmCall.run_id == run_id)
            .group_by(LlmCall.step, LlmCall.model)
            .order_by(LlmCall.step, LlmCall.model)
        ).all()
    keys = ["step", "model", "calls", "documents", "input", "output", "cache_write", "cache_read"]
    return [dict(zip([*keys, "cost_usd"], row, strict=True)) for row in rows]


def truncated_count(run_id: uuid.UUID) -> int:
    with session_scope() as session:
        return session.scalar(
            select(func.count(func.distinct(LlmCall.document_id))).where(
                LlmCall.run_id == run_id, LlmCall.stop_reason == "max_tokens"
            )
        )


def _print_run(run: ExtractRun) -> None:
    counts: dict[str, int] = {}
    for result in run.results.values():
        counts[result] = counts.get(result, 0) + 1
    print(f"run {run.run_id}: {len(run.results)} document(s): {counts}")
    if run.stopped_by_spend_limit:
        print("stopped: the next call could have gone over the spend limit")
    print(
        f"{'step':8} {'model':22} {'calls':>5} {'docs':>4} {'input':>8} {'output':>8} "
        f"{'cache_w':>8} {'cache_r':>8} {'cost $':>10}"
    )
    for r in cost_table(run.run_id):
        print(
            f"{r['step']:8} {r['model']:22} {r['calls']:>5} {r['documents']:>4} "
            f"{r['input']:>8} {r['output']:>8} {r['cache_write']:>8} {r['cache_read']:>8} "
            f"{r['cost_usd']:>10.6f}"
        )
    print(f"total spent: ${run.spent:.6f}")
    print(f"truncated (hit max_tokens, sent to review): {truncated_count(run.run_id)}")


def run_batch_cli(settings: Settings, args: argparse.Namespace) -> None:
    import anthropic

    from app.extraction.batch import BatchRunner, cost_estimates
    from app.extraction.doctext import load_doc_text

    max_spend = Decimal(
        str(args.max_spend if args.max_spend is not None else settings.max_spend_usd)
    )
    ids = select_documents(args.limit, args.doc, args.force)
    if args.plan_only:
        with session_scope() as session:
            docs = {i: load_doc_text(session, i) for i in ids}
        print(
            f"{len(ids)} documents, every one counted as an NDA (the most expensive type). Worst "
            f"case at batch prices, with max_tokens cut to the batch bounds: "
            f"${cost_estimates(settings, docs):.4f} (this is what --max-spend is checked against)"
        )
        return
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key or None)
    runner = BatchRunner(client, settings, args.batch)
    run = ExtractRun(run_id=uuid.uuid4())
    outcomes = runner.run(ids, max_spend, run.run_id, args.force)
    run.results = {i: o.result for i, o in outcomes.items()}
    run.spent = sum((o.cost_usd for o in outcomes.values()), Decimal(0))
    _print_run(run)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    rq = sub.add_parser("requeue", help="queue parsing again for documents in these statuses")
    rq.add_argument("--status", default="received", help="comma-separated, e.g. received,failed")
    ex = sub.add_parser("extract", help="classify + extract parsed documents (calls the API)")
    ex.add_argument("--limit", type=int, help="at most this many documents")
    ex.add_argument(
        "--max-spend", type=Decimal, help="USD cap for this run (default MAX_SPEND_USD)"
    )
    ex.add_argument("--doc", type=uuid.UUID, action="append", default=[], help="only this document")
    ex.add_argument("--force", action="store_true", help="re-run documents already extracted")
    ex.add_argument(
        "--plan-only",
        action="store_true",
        help="with --batch: print the worst-case cost at batch prices and stop (no API calls)",
    )
    ex.add_argument(
        "--batch",
        type=Path,
        metavar="WORKDIR",
        help="use the Batch API (half price, results within hours); answers and batch ids are "
        "kept in WORKDIR so a stopped run resumes without paying twice",
    )
    args = parser.parse_args(argv)

    if args.command == "requeue":
        statuses = [s.strip() for s in args.status.split(",") if s.strip()]
        for s in statuses:
            Status(s)  # fail early on a typo
        print(f"queued {requeue(statuses)} document(s)")
    elif args.command == "extract":
        from app.extraction.anthropic_extractor import AnthropicExtractor

        settings = get_settings()
        if args.batch:
            run_batch_cli(settings, args)
            return
        run = extract(
            AnthropicExtractor(settings),
            settings,
            limit=args.limit,
            max_spend=args.max_spend,
            doc_ids=args.doc,
            force=args.force,
        )
        _print_run(run)


if __name__ == "__main__":
    main()
