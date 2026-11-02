"""Test doubles, so unit tests never load Docling or call the Anthropic API."""

import json
from pathlib import Path

from app.extraction.anthropic_extractor import validate
from app.extraction.base import CallPlan, LlmResult, Usage
from app.extraction.doctext import DocText, normalize
from app.models import BlockSource, LlmOutcome, LlmStep
from app.parsing.base import Block, ParseResult


class FakeParser:
    """Returns two blocks per page; can be told to fail or to report a partial parse."""

    def __init__(
        self,
        pages: int = 1,
        error: Exception | None = None,
        failed_pages: list[int] | None = None,
    ) -> None:
        self.pages = pages
        self.error = error
        self.failed_pages = failed_pages or []
        self.calls: list[Path] = []

    def warm_up(self) -> None:
        pass

    def parse(self, path: Path) -> ParseResult:
        self.calls.append(path)
        if self.error:
            raise self.error
        blocks = [
            Block(
                page=page,
                seq=i,
                bbox=(72.0, 72.0 + 20 * i, 540.0, 90.0 + 20 * i),
                text=f"page {page} block {i}",
                label="text",
                source=BlockSource.TEXT_LAYER,
            )
            for i, page in enumerate(p for p in range(1, self.pages + 1) for _ in range(2))
        ]
        return ParseResult(page_count=self.pages, blocks=blocks, failed_pages=self.failed_pages)


class RecordingQueue:
    """Stands in for Celery: remembers which documents were queued."""

    def __init__(self, fail: bool = False) -> None:
        self.queued: list = []
        self.fail = fail

    def __call__(self, document_id) -> None:
        if self.fail:
            raise ConnectionError("redis is down")
        self.queued.append(document_id)


# --- Extraction ---------------------------------------------------------------------------------

NOT_IN_DOC = "This sentence does not appear anywhere in the document."


class FakeExtractor:
    """Answers like a careful model would, from a manifest entry (the truth) and the parsed text.

    Knobs for the unhappy paths:
    - invalid_times: return broken JSON this many times first
    - refuse: answer with stop_reason "refusal"
    - error: raise this (an LlmCallFailed) instead of answering
    - classification / fields: per-key overrides merged into the answer
    - invent_quotes: replace every quote with text that isn't in the document
    """

    def __init__(
        self,
        entry: dict,
        *,
        invalid_times: int = 0,
        refuse: bool = False,
        error: Exception | None = None,
        classification: dict | None = None,
        fields: dict[str, dict] | None = None,
        invent_quotes: bool = False,
        usage: Usage | None = None,
    ) -> None:
        self.entry = entry
        self.invalid_times = invalid_times
        self.refuse = refuse
        self.error = error
        self.classification = classification or {}
        self.fields = fields or {}
        self.invent_quotes = invent_quotes
        self.usage = usage or Usage(input_tokens=1000, output_tokens=200)
        self.calls: list[CallPlan] = []

    def call(self, plan: CallPlan, doc: DocText) -> LlmResult:
        self.calls.append(plan)
        if self.error:
            raise self.error
        data = self._classify(doc) if plan.step == LlmStep.CLASSIFY else self._extract(doc, plan)
        raw = json.dumps(data)
        if self.invalid_times > 0:
            self.invalid_times -= 1
            raw = raw[: len(raw) // 2]  # cut off mid-JSON
        result = LlmResult(
            outcome=LlmOutcome.OK,
            model=plan.model,
            usage=Usage(**vars(self.usage)),
            stop_reason="refusal" if self.refuse else "end_turn",
            raw_text=raw,
        )
        return validate(result, plan, doc)

    def _classify(self, doc: DocText) -> dict:
        first = doc.pages[0].blocks[0].text if doc.pages and doc.pages[0].blocks else ""
        answer = {
            "doc_type": self.entry["doc_type"],
            "subtype": self.entry.get("subtype"),
            "confidence": 0.95,
            "page": 1,
            "quote": NOT_IN_DOC if self.invent_quotes else first,
        }
        return answer | self.classification

    def _extract(self, doc: DocText, plan: CallPlan) -> dict:
        answer = {}
        for name in plan.output.model_fields:
            value = self.entry["fields"].get(name)
            quote = self.entry.get("evidence", {}).get(name)
            page = next(
                (p.number for p in doc.pages if quote and normalize(quote) in p.norm_text), 1
            )
            if value is None:
                answer[name] = {"value": None, "page": None, "quote": None, "confidence": 0.9}
            else:
                quote = NOT_IN_DOC if self.invent_quotes else quote
                answer[name] = {"value": value, "page": page, "quote": quote, "confidence": 0.95}
            answer[name] |= self.fields.get(name, {})
        return answer
