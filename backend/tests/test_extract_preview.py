"""The preview report's arithmetic, on fake-extractor results (no API calls)."""

import pytest

from scripts.extract_preview import build_report, write_markdown
from tests.docs import INJECTION_NDA, LEGAL_NOTICE, NDA, SIDE_LETTER, add_parsed_doc
from tests.fakes import FakeExtractor
from tests.test_extract_run import _run

pytestmark = pytest.mark.db


def test_report_counts_hits_misses_and_cost_per_model(db, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_MODEL_CLASSIFY", "claude-haiku-5-5")
    monkeypatch.setenv("ANTHROPIC_MODEL_EXTRACT", "claude-sonnet-5-5")
    sample = [NDA, LEGAL_NOTICE, SIDE_LETTER, INJECTION_NDA]
    ids = {}
    for entry in sample:
        ids[entry["doc_id"]] = add_parsed_doc(
            entry, source="ocr" if entry.get("scanned") else "text_layer"
        )
    _run(ids[NDA["doc_id"]], FakeExtractor(NDA))
    # Wrong type for the notice: all its fields count as misses.
    _run(
        ids[LEGAL_NOTICE["doc_id"]],
        FakeExtractor(
            LEGAL_NOTICE, classification={"doc_type": "legal_notice", "subtype": "demand_letter"}
        ),
    )
    # One wrong value in the side letter.
    wrong = {"commitment_usd": {"value": SIDE_LETTER["fields"]["commitment_usd"] + 1}}
    _run(ids[SIDE_LETTER["doc_id"]], FakeExtractor(SIDE_LETTER, fields=wrong))
    _run(ids[INJECTION_NDA["doc_id"]], FakeExtractor(INJECTION_NDA))

    report = build_report(sample, ids, run_id=None)

    assert report["classification"]["type_accuracy"]["all"] == "4/4 (100%)"
    assert report["classification"]["subtype_accuracy"]["all"] == "3/4 (75%)"
    n_nda, n_side, n_notice = (len(e["fields"]) for e in (NDA, SIDE_LETTER, LEGAL_NOTICE))
    total = 2 * n_nda + n_side + n_notice
    hits = 2 * n_nda + n_side - 1
    assert report["field_exact_match"]["all"].startswith(f"{hits}/{total} ")
    assert (
        report["field_exact_match"]["scanned"]
        == f"{n_side - 1}/{n_side} ({100 * (n_side - 1) / n_side:.0f}%)"
    )
    assert report["nda_recall"]["classified_nda"] == "2/2 (100%)"
    assert report["injection_nda"]["reasons"] == ["suspected_prompt_injection"]
    assert report["injection_nda"]["governing_law"] == "Delaware"
    per_model = report["cost"]["per_model"]
    assert per_model["claude-sonnet-5-5"]["documents"] == 2  # only the NDAs
    assert per_model["claude-haiku-5-5"]["documents"] == 4
    assert "| claude-sonnet-5-5 | 2 |" in write_markdown(report)
