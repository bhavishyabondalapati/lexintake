"""Replay of real model responses (recorded by `extract_preview --record`, synthetic data only).

No API and no database: each fixture holds the parsed text blocks and the model's raw answers,
which go through the same validation, citation checks and playbook check as live calls.
"""

import json

import pytest

from app.config import Settings
from app.extraction.anthropic_extractor import validate
from app.extraction.base import LlmResult, plan_classify, plan_extract
from app.extraction.doctext import BlockText, doc_text_from_blocks
from app.extraction.schemas import DocType, Subtype
from app.extraction.verify import check_classification, check_fields, injection_signals
from app.models import LlmOutcome
from app.playbook import check_playbook, evaluate, load_playbook
from tests.conftest import FIXTURES

RECORDED = sorted((FIXTURES / "llm").glob("*.json"))
SETTINGS = Settings(
    _env_file=None,
    anthropic_model_classify="claude-haiku-5-5",
    anthropic_model_extract="claude-sonnet-5-5",
)


def load(path):
    fx = json.loads(path.read_text())
    blocks = [
        (b["page"], BlockText(i, b["text"], b["source"])) for i, b in enumerate(fx["blocks"], 1)
    ]
    return fx, doc_text_from_blocks(fx["page_count"], blocks)


def replay(step_plan, response, doc):
    result = LlmResult(
        outcome=LlmOutcome.OK,
        model=response["model"],
        stop_reason=response["stop_reason"],
        raw_text=response["raw_output"],
    )
    return validate(result, step_plan, doc)


def test_six_recordings_exist_and_hold_no_secrets():
    assert len(RECORDED) == 6
    for path in RECORDED:
        text = path.read_text().lower()
        assert "sk-ant" not in text and "api_key" not in text and "req_01" not in text


@pytest.mark.parametrize("path", RECORDED, ids=lambda p: p.stem)
def test_recorded_responses_pass_validation_and_match_the_truth(path):
    fx, doc = load(path)
    truth = fx["truth"]

    classify = replay(plan_classify(SETTINGS, doc), fx["responses"]["classify"], doc)
    assert classify.outcome == LlmOutcome.OK, classify.error
    assert classify.output.doc_type.value == truth["doc_type"]
    assert check_classification(doc, classify.output).status == "verified"

    doc_type = DocType(truth["doc_type"])
    subtype = Subtype(truth["subtype"]) if truth["subtype"] else None
    extract = replay(
        plan_extract(SETTINGS, doc, doc_type, subtype), fx["responses"]["extract"], doc
    )
    assert extract.outcome == LlmOutcome.OK, extract.error
    checks = {c.name: c for c in check_fields(doc, extract.output, doc_type)}
    for name, expected in truth["fields"].items():
        got = checks[name].value
        assert (got.isoformat() if hasattr(got, "isoformat") else got) == expected, name
        assert checks[name].status in ("verified", "absent"), (name, checks[name].status)


@pytest.mark.parametrize(
    "path", [p for p in RECORDED if "nda" in p.stem or p.stem in ("syn_00042", "syn_00206")]
)
def test_recorded_nda_playbook_result_matches_the_truth(path):
    fx, doc = load(path)
    if fx["truth"]["doc_type"] != "nda":
        pytest.skip("not an NDA")
    extract = replay(
        plan_extract(SETTINGS, doc, DocType.NDA, None), fx["responses"]["extract"], doc
    )
    checks = {c.name: c.to_json() for c in check_fields(doc, extract.output, DocType.NDA)}

    results = check_playbook(load_playbook(), checks)

    deviations = [r.rule_id for r in results if r.outcome == "deviation"]
    assert deviations == evaluate(load_playbook(), fx["truth"]["fields"])


def test_the_planted_instruction_is_flagged_in_the_recorded_injection_document():
    _, doc = load(FIXTURES / "llm" / "injection_nda.json")

    assert injection_signals(doc)
