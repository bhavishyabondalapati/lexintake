"""The Extractor interface. The pipeline only knows this, so tests can swap in a fake (or a replay
of recorded real responses) and never call the API.

A call happens in two halves: `plan_*()` decides *what* to send (model, prompts, schema, limits;
the same for every implementation), and `Extractor.call()` sends it. In between, the spend guard
looks at the plan's worst-case cost and can stop the run before anything is sent.
"""

import json
from dataclasses import dataclass, field
from functools import cached_property
from typing import Protocol

import anthropic
from pydantic import BaseModel

from app.config import Settings
from app.extraction import prompts
from app.extraction.doctext import DocText
from app.extraction.pricing import worst_case_usd
from app.extraction.schemas import Classification, DocType, Subtype, cited_model, field_model
from app.models import LlmOutcome, LlmStep

CLASSIFY_MAX_TOKENS = 2_000
EXTRACT_MAX_TOKENS = {True: 8_000, False: 3_000}  # NDA (full fields) / other types (small sets)
CHARS_PER_TOKEN = 3  # deliberately low, so the token estimate (and the worst case) is high


class ModelNotConfigured(Exception):
    """A model setting is empty. Never guessed: the document fails with this message."""


@dataclass(frozen=True)
class CallPlan:
    step: LlmStep
    model: str
    system: str
    user: str
    output: type[BaseModel]
    max_tokens: int
    effort: str = ""

    @cached_property
    def schema(self) -> dict:
        return anthropic.transform_schema(self.output)

    @property
    def estimated_input_tokens(self) -> int:
        chars = len(self.system) + len(self.user) + len(json.dumps(self.schema))
        return chars // CHARS_PER_TOKEN + 1

    @property
    def worst_case_usd(self):
        return worst_case_usd(self.model, self.estimated_input_tokens, self.max_tokens)


@dataclass
class Usage:
    input_tokens: int = 0  # uncached input
    output_tokens: int = 0  # includes thinking tokens
    cache_write_tokens: int = 0
    cache_read_tokens: int = 0


@dataclass
class LlmResult:
    outcome: LlmOutcome
    model: str
    output: BaseModel | None = None  # validated, when outcome is OK
    usage: Usage = field(default_factory=Usage)
    stop_reason: str | None = None
    request_id: str | None = None
    latency_ms: int = 0
    error: str | None = None  # why the output was rejected
    raw_text: str | None = None  # the model's JSON, kept for recording fixtures


class LlmCallFailed(Exception):
    """The API call itself failed (no usable response). `retryable` = worth trying again later."""

    def __init__(self, message: str, retryable: bool, model: str = "") -> None:
        super().__init__(message)
        self.retryable = retryable
        self.model = model


class Extractor(Protocol):
    def call(self, plan: CallPlan, doc: DocText) -> LlmResult:
        """Send the plan. Returns a result (OK, invalid output or refused) or raises
        LlmCallFailed. `doc` gives the page count for validation (and the fake its answers)."""
        ...


def _model(settings: Settings, name: str) -> str:
    model = getattr(settings, name)
    if not model:
        raise ModelNotConfigured(f"{name.upper()} is not set (see .env.example)")
    return model


def plan_classify(settings: Settings, doc: DocText) -> CallPlan:
    return CallPlan(
        step=LlmStep.CLASSIFY,
        model=_model(settings, "anthropic_model_classify"),
        system=prompts.classify_system(),
        user=prompts.classify_user(doc),
        output=Classification,
        max_tokens=CLASSIFY_MAX_TOKENS,
        effort=settings.anthropic_effort_classify,
    )


def plan_extract(
    settings: Settings, doc: DocText, doc_type: DocType, subtype: Subtype | None
) -> CallPlan:
    """NDAs get the full field set from the extract model; every other type gets its small field
    set from the (cheaper) classify model."""
    nda = doc_type == DocType.NDA
    return CallPlan(
        step=LlmStep.EXTRACT,
        model=_model(settings, "anthropic_model_extract" if nda else "anthropic_model_classify"),
        system=prompts.extract_system(),
        user=prompts.extract_user(doc, doc_type, subtype),
        output=cited_model(field_model(doc_type, subtype)),
        max_tokens=EXTRACT_MAX_TOKENS[nda],
        effort=settings.anthropic_effort_extract if nda else settings.anthropic_effort_classify,
    )
