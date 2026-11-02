"""Batch mode: classify and extract many documents through the Anthropic Batch API (half price).

Same plans, schemas, validation, checks and routing as the live path. The only difference is how
the model's answers arrive:

  1. one batch of classify requests, then
  2. one batch of extract requests (only for documents whose classification is not in doubt),
  3. `run_extraction` per document, with an extractor that hands out the stored answers instead of
     calling the API. Cost is recorded per call at batch prices (`price_factor` 0.5).

Safety:
- The spend cap is checked against the batch's worst case BEFORE anything is submitted.
- Every answer is written to `workdir` as soon as a batch finishes, and the batch ids are saved
  first, so a crash or a closed laptop can resume instead of paying twice.
- A request the API rejects because the schema is too big for its grammar compiler is resubmitted
  once in a second batch without the grammar (rejected requests are not billed).
"""

import json
import logging
import time
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace
from decimal import Decimal
from pathlib import Path
from typing import Any

from anthropic.types import Message

from app.config import Settings
from app.db import session_scope
from app.extraction.anthropic_extractor import (
    GRAMMAR_TOO_LARGE,
    request_params,
    result_from_message,
)
from app.extraction.base import (
    CallPlan,
    LlmCallFailed,
    LlmResult,
    plan_classify,
    plan_extract,
)
from app.extraction.doctext import DocText, load_doc_text
from app.extraction.pricing import SpendGuard
from app.extraction.route import classification_reasons
from app.extraction.run import Outcome, RunContext, run_extraction
from app.extraction.schemas import Classification
from app.extraction.verify import check_classification, nda_signals
from app.models import LlmStep

log = logging.getLogger(__name__)

BATCH_PRICE_FACTOR = Decimal("0.5")
# Batch requests are sent with max_tokens cut to these bounds (about 3x what the preview runs
# showed: classify ~60, NDA extract ~850, other ~450), so the cost bound is a true worst case. A
# response that hits the limit is routed to needs_review as "truncated".
OUTPUT_BOUND = {"classify": 400, "extract_nda": 2_500, "extract_other": 1_200}
POLL_SECONDS = 30


@dataclass
class BatchResults:
    """What the replay extractor serves: per (document, step) a result, or the reason it failed."""

    answers: dict[tuple[uuid.UUID, str], LlmResult | LlmCallFailed] = field(default_factory=dict)


class ReplayExtractor:
    """Extractor that answers from stored batch results. Tell it which document is next."""

    def __init__(self, results: BatchResults) -> None:
        self.results = results
        self.document_id: uuid.UUID | None = None

    def call(self, plan: CallPlan, doc: DocText) -> LlmResult:
        answer = self.results.answers.get((self.document_id, plan.step.value))
        if answer is None:
            raise LlmCallFailed("no batch result for this request", retryable=False)
        if isinstance(answer, LlmCallFailed):
            raise answer
        return answer


def bounded(plan: CallPlan) -> CallPlan:
    """The plan with max_tokens cut to the batch bound for its step."""
    if plan.step == LlmStep.CLASSIFY:
        bound = OUTPUT_BOUND["classify"]
    else:
        bound = OUTPUT_BOUND["extract_nda" if plan.max_tokens > 3_000 else "extract_other"]
    return replace(plan, max_tokens=min(plan.max_tokens, bound))


def custom_id(document_id: uuid.UUID, step: LlmStep) -> str:
    return f"{document_id}_{step.value}"  # the API allows only letters, digits, - and _


def split_custom_id(value: str) -> tuple[uuid.UUID, str]:
    document_id, step = value.rsplit("_", 1)
    return uuid.UUID(document_id), step


class BatchRunner:
    def __init__(
        self,
        client: Any,
        settings: Settings,
        workdir: Path,
        sleep: Callable[[float], None] = time.sleep,
        poll_seconds: float = POLL_SECONDS,
    ) -> None:
        self.client = client
        self.settings = settings
        self.workdir = Path(workdir)
        self.workdir.mkdir(parents=True, exist_ok=True)
        self.sleep, self.poll_seconds = sleep, poll_seconds
        self.state_path = self.workdir / "state.json"
        self.state: dict[str, str] = (
            json.loads(self.state_path.read_text()) if self.state_path.exists() else {}
        )

    # --- one batch: submit (or resume), wait, download ----------------------------------------

    def _save_state(self) -> None:
        self.state_path.write_text(json.dumps(self.state, indent=2))

    def _run_batch(self, name: str, plans: dict[str, CallPlan], strict: bool) -> dict[str, Any]:
        """Returns custom_id -> the result entry as a plain dict. Saved to disk before it is
        returned."""
        out = self.workdir / f"{name}.jsonl"
        if out.exists():  # downloaded on an earlier run
            lines = map(json.loads, out.read_text().splitlines())
            return {line["custom_id"]: line["raw"] for line in lines}
        if name not in self.state:
            requests = [
                {"custom_id": cid, "params": request_params(plan, strict)}
                for cid, plan in plans.items()
            ]
            batch = self.client.messages.batches.create(requests=requests)
            self.state[name] = batch.id
            self._save_state()  # before waiting: a crash from here on resumes this batch
            log.info("batch %s: %s requests submitted as %s", name, len(requests), batch.id)
        batch_id = self.state[name]
        while self.client.messages.batches.retrieve(batch_id).processing_status != "ended":
            self.sleep(self.poll_seconds)
        entries = {}
        for entry in self.client.messages.batches.results(batch_id):
            entries[entry.custom_id] = entry.model_dump(mode="json")
        lines = (json.dumps({"custom_id": c, "raw": raw}) for c, raw in entries.items())
        out.write_text("\n".join(lines) + "\n")
        return entries

    def _collect(
        self,
        name: str,
        plans: dict[str, CallPlan],
        docs: dict[uuid.UUID, DocText],
        results: BatchResults,
    ) -> None:
        """Run a batch and file each answer under (document, step). Requests the API rejected for
        a too-big grammar go once more in a second batch, asked for in words instead."""
        entries = self._run_batch(name, plans, strict=True)
        retry: dict[str, CallPlan] = {}
        for cid, entry in entries.items():
            plan = plans[cid]
            outcome = _outcome(entry)
            if outcome.get("grammar"):
                retry[cid] = plan
            else:
                self._file(cid, plan, docs, results, outcome)
        if retry:
            second = self._run_batch(f"{name}-nogrammar", retry, strict=False)
            for cid, entry in second.items():
                self._file(cid, retry[cid], docs, results, _outcome(entry))

    @staticmethod
    def _file(cid, plan, docs, results, outcome) -> None:
        document_id, step = split_custom_id(cid)
        if "message" in outcome:
            results.answers[(document_id, step)] = result_from_message(
                outcome["message"], plan, docs[document_id]
            )
        else:
            results.answers[(document_id, step)] = LlmCallFailed(
                outcome["error"], retryable=False, model=plan.model
            )

    # --- the whole run -------------------------------------------------------------------------

    def run(
        self,
        document_ids: Iterable[uuid.UUID],
        max_spend: Decimal,
        run_id: uuid.UUID | None = None,
        force: bool = False,
    ) -> dict[uuid.UUID, Outcome]:
        run_id = run_id or uuid.uuid4()
        with session_scope() as session:
            docs = {i: load_doc_text(session, i) for i in document_ids}
        classify = {
            custom_id(i, LlmStep.CLASSIFY): bounded(plan_classify(self.settings, d))
            for i, d in docs.items()
        }
        self._check_spend(classify.values(), max_spend, "classify")
        results = BatchResults()
        self._collect("classify", classify, docs, results)

        extract: dict[str, CallPlan] = {}
        for i, d in docs.items():
            c = results.answers.get((i, LlmStep.CLASSIFY.value))
            if not isinstance(c, LlmResult) or not isinstance(c.output, Classification):
                continue  # failed or unusable: run_extraction sends it to review / fails it
            check = check_classification(d, c.output)
            if classification_reasons(self.settings, c.output, check, nda_signals(d)):
                continue  # the type is in doubt: don't pay for step 2
            extract[custom_id(i, LlmStep.EXTRACT)] = bounded(
                plan_extract(self.settings, d, c.output.doc_type, c.output.subtype)
            )
        self._check_spend(extract.values(), max_spend, "extract")
        if extract:
            self._collect("extract", extract, docs, results)

        replay = ReplayExtractor(results)
        # The money was committed when the batches were submitted (and capped there), so the
        # per-call guard of the live path has nothing left to protect.
        ctx = RunContext(
            replay,
            SpendGuard("1000000"),
            self.settings,
            run_id=run_id,
            actor="batch:extract",
            price_factor=BATCH_PRICE_FACTOR,
        )
        outcomes = {}
        for i in docs:
            replay.document_id = i
            outcomes[i] = run_extraction(i, ctx, force=force)
        return outcomes

    def _check_spend(self, plans: Iterable[CallPlan], max_spend: Decimal, what: str) -> None:
        worst = sum((budget_bound(p) for p in plans), Decimal(0))
        spent = self.state.get("_committed", "0")
        total = Decimal(spent) + worst
        if f"{what}-checked" in self.state:
            return
        if total > max_spend:
            raise SystemExit(
                f"batch {what}: worst case ${worst:.4f} (batch prices) on top of ${spent} already "
                f"committed would pass the ${max_spend} cap; nothing was sent"
            )
        self.state["_committed"] = str(total)
        self.state[f"{what}-checked"] = "1"
        self._save_state()


def budget_bound(plan: CallPlan) -> Decimal:
    """The most a (bounded) call can cost at batch prices: all input uncached, max_tokens out."""
    return plan.worst_case_usd * BATCH_PRICE_FACTOR


def cost_estimates(settings: Settings, docs: dict[uuid.UUID, DocText]) -> Decimal:
    """For `--plan-only`: the worst case at batch prices if every document were an NDA (the most
    expensive type). It is the same number the spend cap is checked against."""
    from app.extraction.schemas import DocType

    return sum(
        (
            budget_bound(bounded(p))
            for d in docs.values()
            for p in (plan_classify(settings, d), plan_extract(settings, d, DocType.NDA, None))
        ),
        Decimal(0),
    )


def _outcome(raw: dict) -> dict:
    """One result entry (a dict) -> {"message": ...} or {"grammar": True} or {"error": ...}."""
    result = raw["result"]
    if result["type"] == "succeeded":
        return {"message": Message.model_validate(result["message"])}
    detail = json.dumps(result.get("error", {}))
    if GRAMMAR_TOO_LARGE in detail:
        return {"grammar": True}
    return {"error": f"batch request {result['type']}: {detail}"[:2000]}
