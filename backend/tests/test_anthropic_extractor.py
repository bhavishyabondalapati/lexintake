"""The real extractor's request and response handling, against a stub client (nothing is sent)."""

import json
from decimal import Decimal
from types import SimpleNamespace

import anthropic
import httpx2
import pytest

from app.config import Settings
from app.extraction.anthropic_extractor import AnthropicExtractor, request_params
from app.extraction.base import LlmCallFailed, plan_classify, plan_extract
from app.extraction.doctext import BlockText, doc_text_from_blocks
from app.extraction.pricing import (
    SpendGuard,
    SpendLimitReached,
    UnknownModelPrice,
    cost_usd,
    worst_case_usd,
)
from app.extraction.schemas import DocType, Subtype
from app.models import LlmOutcome

SETTINGS = Settings(
    _env_file=None,
    anthropic_model_classify="claude-haiku-5-5",
    anthropic_model_extract="claude-sonnet-5-5",
)
DOC = doc_text_from_blocks(1, [(1, BlockText(1, "Mutual Confidentiality Agreement", "text_layer"))])
GOOD = {
    "doc_type": "nda",
    "subtype": None,
    "confidence": 0.93,
    "page": 1,
    "quote": "Mutual Confidentiality Agreement",
}


class StubMessages:
    def __init__(self, response=None, error=None):
        self.response, self.error, self.requests = response, error, []

    def create(self, **kwargs):
        self.requests.append(kwargs)
        if self.error:
            raise self.error
        return self.response


def _response(text, stop_reason="end_turn"):
    return SimpleNamespace(
        model="claude-haiku-5-5",
        stop_reason=stop_reason,
        content=[SimpleNamespace(type="thinking"), SimpleNamespace(type="text", text=text)],
        usage=SimpleNamespace(
            input_tokens=300,
            output_tokens=120,
            cache_creation_input_tokens=None,
            cache_read_input_tokens=1500,
        ),
        _request_id="req_123",
    )


def _extractor(**stub):
    messages = StubMessages(**stub)
    return AnthropicExtractor(SETTINGS, client=SimpleNamespace(messages=messages)), messages


def _status_error(cls, code):
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    return cls("boom", response=httpx2.Response(code, request=request), body=None)


def test_request_caches_the_system_prompt_and_asks_for_the_schema():
    extractor, messages = _extractor(response=_response(json.dumps(GOOD)))
    plan = plan_classify(SETTINGS, DOC)

    extractor.call(plan, DOC)

    sent = messages.requests[0]
    assert sent["model"] == "claude-haiku-5-5"
    assert sent["system"] == [
        {"type": "text", "text": plan.system, "cache_control": {"type": "ephemeral"}}
    ]
    assert sent["output_config"]["format"]["type"] == "json_schema"
    assert sent["output_config"]["effort"] == "low"
    assert sent["messages"][0]["content"].startswith('<document pages="1">')
    assert "fallbacks" not in sent  # no refusal fallback to an unconfigured model


def test_response_is_validated_and_usage_including_cache_is_kept():
    extractor, _ = _extractor(response=_response(json.dumps(GOOD)))

    result = extractor.call(plan_classify(SETTINGS, DOC), DOC)

    assert result.outcome == LlmOutcome.OK
    assert result.output.doc_type == DocType.NDA
    assert (result.usage.input_tokens, result.usage.output_tokens) == (300, 120)
    assert (result.usage.cache_write_tokens, result.usage.cache_read_tokens) == (0, 1500)
    assert result.request_id == "req_123"


@pytest.mark.parametrize(
    ("text", "stop_reason", "outcome"),
    [
        ('{"doc_type": "nda"', "max_tokens", LlmOutcome.INVALID_OUTPUT),
        (json.dumps(GOOD | {"page": 7}), "end_turn", LlmOutcome.INVALID_OUTPUT),
        ("", "refusal", LlmOutcome.REFUSED),
    ],
)
def test_unusable_responses(text, stop_reason, outcome):
    extractor, _ = _extractor(response=_response(text, stop_reason))

    result = extractor.call(plan_classify(SETTINGS, DOC), DOC)

    assert result.outcome == outcome and result.output is None and result.error


@pytest.mark.parametrize(
    ("error", "retryable"),
    [
        (_status_error(anthropic.RateLimitError, 429), True),
        (_status_error(anthropic.InternalServerError, 500), True),
        (_status_error(anthropic.BadRequestError, 400), False),
        (_status_error(anthropic.AuthenticationError, 401), False),
        (anthropic.APIConnectionError(request=httpx2.Request("POST", "https://x.test")), True),
    ],
)
def test_api_errors_say_whether_to_retry(error, retryable):
    extractor, _ = _extractor(error=error)

    with pytest.raises(LlmCallFailed) as raised:
        extractor.call(plan_classify(SETTINGS, DOC), DOC)

    assert raised.value.retryable is retryable


def test_nda_extraction_uses_the_extract_model_and_others_the_classify_model():
    nda = plan_extract(SETTINGS, DOC, DocType.NDA, None)
    notice = plan_extract(SETTINGS, DOC, DocType.LEGAL_NOTICE, Subtype.SUBPOENA)

    assert (nda.model, nda.effort, nda.max_tokens) == ("claude-sonnet-5-5", "medium", 8000)
    assert (notice.model, notice.effort) == ("claude-haiku-5-5", "low")
    assert "case_number" in notice.schema["properties"]


def test_cost_uses_cache_prices():
    # Sonnet 5.5: $2 in, $10 out, $2.50 cache write, $0.10 cache read per million tokens.
    assert cost_usd("claude-sonnet-5-5", 1_000_000, 0) == Decimal("2.000000")
    assert cost_usd("claude-sonnet-5-5", 0, 0, cache_read=1_000_000) == Decimal("0.100000")
    assert cost_usd("claude-sonnet-5-5", 100, 200, 300, 400) == Decimal("0.002990")


def test_haiku_long_prompts_cost_more():
    assert cost_usd("claude-haiku-5-5", 100_000, 0) == Decimal("0.010000")
    assert cost_usd("claude-haiku-5-5", 100_002, 0) == Decimal("0.050001")


def test_unknown_model_has_no_price():
    with pytest.raises(UnknownModelPrice):
        cost_usd("some-new-model", 1, 1)


def test_spend_guard_plans_for_the_worst_case():
    guard = SpendGuard("0.10")
    worst = worst_case_usd("claude-sonnet-5-5", 4_000, 8_000)  # 4k in at $2.50 + 8k out at $10

    assert worst == Decimal("0.090000")
    guard.check(worst)
    guard.add(Decimal("0.02"))
    with pytest.raises(SpendLimitReached):
        guard.check(worst)


def test_schema_too_large_for_the_grammar_falls_back_to_a_plain_json_request():
    class Stub:
        def __init__(self):
            self.requests = []

        def create(self, **kwargs):
            self.requests.append(kwargs)
            if "format" in kwargs["output_config"]:
                request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
                raise anthropic.BadRequestError(
                    "The compiled grammar is too large",
                    response=httpx2.Response(400, request=request),
                    body=None,
                )
            return _response("```json\n" + json.dumps(GOOD) + "\n```")

    stub = Stub()
    extractor = AnthropicExtractor(SETTINGS, client=SimpleNamespace(messages=stub))
    plan = plan_classify(SETTINGS, DOC)

    first = extractor.call(plan, DOC)
    second = extractor.call(plan, DOC)

    assert first.outcome == second.outcome == LlmOutcome.OK  # a code fence is tolerated
    assert [("format" in r["output_config"]) for r in stub.requests] == [True, False, False]
    assert "JSON Schema" in stub.requests[1]["messages"][0]["content"]


def test_requests_carry_no_sampling_parameters():
    # The current models don't take temperature / top_p / top_k (the SDK has no such argument
    # any more), so results vary a little between runs and the eval is a single run.
    params = request_params(plan_classify(SETTINGS, DOC))

    assert not {"temperature", "top_p", "top_k"} & params.keys()
