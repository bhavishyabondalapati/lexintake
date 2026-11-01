"""Real Docling on synthetic fixtures. Slow (loads the models), so excluded from the default run.

Run with `make test-slow` (inside the worker image) or `uv run --group parse pytest -m slow`.
"""

import json
import re

import pytest
from rapidfuzz import fuzz

from tests.conftest import FIXTURES

pytestmark = pytest.mark.slow

MANIFEST = {
    d["doc_id"]: d for d in json.loads((FIXTURES / "manifest.json").read_text())["documents"]
}


def collapse(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


@pytest.fixture(scope="module")
def parser():
    from app.parsing.docling_parser import DoclingParser

    p = DoclingParser(device="cpu")
    p.warm_up()
    return p


def full_text(result) -> str:
    return collapse(" ".join(b.text for b in result.blocks))


@pytest.mark.parametrize("doc_id", ["syn_00001", "syn_00010"])
def test_digital_pdf_uses_the_text_layer_and_contains_every_evidence_string(parser, doc_id):
    result = parser.parse(FIXTURES / f"{doc_id}.pdf")

    assert result.page_count == MANIFEST[doc_id]["pages"]
    assert result.ocr_pages == []
    assert result.failed_pages == []
    assert {b.source.value for b in result.blocks} == {"text_layer"}
    assert all(b.ocr_confidence is None for b in result.blocks)
    text = full_text(result)
    for field, evidence in MANIFEST[doc_id]["evidence"].items():
        assert collapse(evidence) in text, f"{field}: {evidence!r} not found"


def test_scanned_pdf_is_ocrd_and_evidence_matches_fuzzily(parser):
    doc = MANIFEST["syn_00002"]
    result = parser.parse(FIXTURES / "syn_00002.pdf")

    assert result.ocr_pages == [1]
    assert {b.source.value for b in result.blocks} == {"ocr"}
    assert all(0.5 < b.ocr_confidence <= 1.0 for b in result.blocks)
    text = full_text(result)
    for field, evidence in doc["evidence"].items():
        score = fuzz.partial_ratio(collapse(evidence), text)
        assert score >= 85, f"{field}: best match {score:.0f} for {evidence!r}"


def test_blocks_have_page_numbers_reading_order_and_boxes_inside_the_page(parser):
    result = parser.parse(FIXTURES / "syn_00010.pdf")

    assert {b.page for b in result.blocks} == {1, 2}
    # Paragraph-level blocks, not whole pages. (Guards against the page images being rendered
    # without fonts, which made the layout model merge each page into a single block.)
    assert len(result.blocks) >= 10
    assert max(len(b.text) for b in result.blocks) < 1000
    assert [b.seq for b in result.blocks] == list(range(len(result.blocks)))
    assert [b.page for b in result.blocks] == sorted(b.page for b in result.blocks)
    for b in result.blocks:
        x0, y0, x1, y1 = b.bbox
        # US Letter, top-left origin: y grows downwards, so y0 (top) < y1 (bottom).
        assert 0 <= x0 < x1 <= 612 and 0 <= y0 < y1 <= 792, b
    first = result.blocks[0]
    assert first.label == "section_header"
    assert first.bbox[1] < 150  # the title is near the TOP of the page


def test_corrupt_pdf_raises_parse_error(parser, tmp_path):
    from app.parsing.base import ParseError

    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"%PDF-1.7\nthis is not really a pdf\n%%EOF")

    with pytest.raises(ParseError):
        parser.parse(broken)
