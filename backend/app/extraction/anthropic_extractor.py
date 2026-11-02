"""The real Extractor: one Messages API call per plan.

- Structured outputs (`output_config.format`): the API returns JSON for the plan's schema. It can
  still be unusable (cut off at max_tokens, refused, or failing a Pydantic rule the JSON schema
  can't express), so the result is validated again here.
- Prompt caching: the system prompt (fixed instructions) carries a cache marker, so after the
  first call each step reads it from the cache at a fraction of the price.
- Retries: the SDK itself retries 408/409/429/5xx and dropped connections with exponential
  backoff (ANTHROPIC_MAX_RETRIES). What still fails comes out as LlmCallFailed.
- No refusal fallback: it would switch to a model nobody configured and muddle the per-model
  cost numbers. A refusal goes to needs_review instead.
"""

import json
import re
import time

import anthropic
from pydantic import ValidationError

from app.config import Settings
from app.extraction.base import CallPlan, LlmCallFailed, LlmResult, Usage
from app.extraction.doctext import DocText
from app.models import LlmOutcome

MAX_ERROR_CHARS = 2000
# Status codes worth retrying later (the SDK has already retried them a few times).
RETRYABLE_STATUS = {408, 409, 429}
GRAMMAR_TOO_LARGE = "compiled grammar is too large"
FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$")


class AnthropicExtractor:
    def __init__(self, settings: Settings, client: anthropic.Anthropic | None = None) -> None:
        self.client = client or anthropic.Anthropic(
            api_key=settings.anthropic_api_key or None,  # None: the SDK looks in the environment
            max_retries=settings.anthropic_max_retries,
            timeout=120.0,
        )
        # (model, schema size) pairs whose schema the API refused to compile into a grammar.
        self._ungrammared: set[tuple[str, int]] = set()

    def _create(self, plan: CallPlan, strict: bool):
        return self.client.messages.create(**request_params(plan, strict))

    def call(self, plan: CallPlan, doc: DocText) -> LlmResult:
        started = time.monotonic()
        key = (plan.model, len(json.dumps(plan.schema)))
        strict = key not in self._ungrammared
        try:
            try:
                response = self._create(plan, strict)
            except anthropic.BadRequestError as e:
                # The NDA schema (10 cited fields) is too big for the API's grammar compiler.
                # Rejected requests aren't billed, so retry once without the grammar.
                if not (strict and GRAMMAR_TOO_LARGE in str(e)):
                    raise
                self._ungrammared.add(key)
                strict = False
                response = self._create(plan, strict)
        except anthropic.APIStatusError as e:
            retryable = e.status_code in RETRYABLE_STATUS or e.status_code >= 500
            raise LlmCallFailed(
                f"{type(e).__name__} {e.status_code}: {e.message}"[:MAX_ERROR_CHARS],
                retryable=retryable,
                model=plan.model,
            ) from e
        except anthropic.APIConnectionError as e:  # includes timeouts
            raise LlmCallFailed(f"{type(e).__name__}: {e}", retryable=True, model=plan.model) from e
        latency_ms = int((time.monotonic() - started) * 1000)

        return result_from_message(response, plan, doc, latency_ms)


def request_params(plan: CallPlan, strict: bool = True) -> dict:
    """The Messages API parameters for a plan. The batch API takes the same ones."""
    output_config: dict = {}
    user = plan.user
    if strict:
        output_config["format"] = {"type": "json_schema", "schema": plan.schema}
    else:
        # Same schema, but asked for in words and checked by Pydantic afterwards.
        user += (
            "\n\nReturn only a JSON object (no prose, no code fence) that matches this JSON "
            f"Schema:\n{json.dumps(plan.schema)}"
        )
    if plan.effort:
        output_config["effort"] = plan.effort
    return {
        "model": plan.model,
        "max_tokens": plan.max_tokens,
        "system": [{"type": "text", "text": plan.system, "cache_control": {"type": "ephemeral"}}],
        "messages": [{"role": "user", "content": user}],
        "output_config": output_config,
    }


def result_from_message(response, plan: CallPlan, doc: DocText, latency_ms: int = 0) -> LlmResult:
    """A Messages API response (or one batch result's message) -> a validated LlmResult."""
    usage = response.usage
    result = LlmResult(
        outcome=LlmOutcome.OK,
        model=response.model,
        usage=Usage(
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_write_tokens=usage.cache_creation_input_tokens or 0,
            cache_read_tokens=usage.cache_read_input_tokens or 0,
        ),
        stop_reason=response.stop_reason,
        request_id=getattr(response, "_request_id", None),
        latency_ms=latency_ms,
        raw_text=FENCE.sub("", "".join(b.text for b in response.content if b.type == "text")),
    )
    return validate(result, plan, doc)


def validate(result: LlmResult, plan: CallPlan, doc: DocText) -> LlmResult:
    """Turn the raw JSON into the plan's Pydantic model, or mark the result unusable. Shared with
    the replay extractor in the tests, so recorded responses go through the same checks."""
    if result.stop_reason == "refusal":
        result.outcome, result.error = LlmOutcome.REFUSED, "the model declined to answer"
        return result
    if result.stop_reason == "max_tokens":
        result.outcome = LlmOutcome.INVALID_OUTPUT
        result.error = f"output cut off at max_tokens={plan.max_tokens}"
        return result
    try:
        result.output = plan.output.model_validate_json(
            result.raw_text or "", context={"page_count": doc.page_count}
        )
    except ValidationError as e:
        result.outcome, result.error = LlmOutcome.INVALID_OUTPUT, str(e)[:MAX_ERROR_CHARS]
    return result
