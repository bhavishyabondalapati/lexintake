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


class AnthropicExtractor:
    def __init__(self, settings: Settings, client: anthropic.Anthropic | None = None) -> None:
        self.client = client or anthropic.Anthropic(
            api_key=settings.anthropic_api_key or None,  # None: the SDK looks in the environment
            max_retries=settings.anthropic_max_retries,
            timeout=120.0,
        )

    def call(self, plan: CallPlan, doc: DocText) -> LlmResult:
        output_config: dict = {"format": {"type": "json_schema", "schema": plan.schema}}
        if plan.effort:
            output_config["effort"] = plan.effort
        started = time.monotonic()
        try:
            response = self.client.messages.create(
                model=plan.model,
                max_tokens=plan.max_tokens,
                system=[
                    {"type": "text", "text": plan.system, "cache_control": {"type": "ephemeral"}}
                ],
                messages=[{"role": "user", "content": plan.user}],
                output_config=output_config,
            )
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
            raw_text="".join(b.text for b in response.content if b.type == "text"),
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
