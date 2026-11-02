import json
from datetime import date

import pytest

from app.extraction.doctext import BlockText, doc_text_from_blocks, normalize
from app.extraction.schemas import Classification, DocType, NdaFields, cited_model
from app.extraction.verify import (
    check_classification,
    check_fields,
    find_span,
    nda_signals,
    number_words,
    value_in_quote,
)
from tests.conftest import FIXTURES

MANIFEST = {
    d["doc_id"]: d for d in json.loads((FIXTURES / "manifest.json").read_text())["documents"]
}
NDA = MANIFEST["syn_00010"]


def _doc(pages: list[list[str]], source="text_layer"):
    blocks, bid = [], 0
    for number, texts in enumerate(pages, 1):
        for text in texts:
            bid += 1
            blocks.append((number, BlockText(bid, text, source, 0.98 if source == "ocr" else None)))
    return doc_text_from_blocks(len(pages), blocks)


def _nda_doc(source="text_layer"):
    """The NDA's evidence sentences as blocks, split over two pages."""
    sentences = list(dict.fromkeys(NDA["evidence"].values()))
    half = len(sentences) // 2
    return _doc([["Confidentiality Agreement", *sentences[:half]], sentences[half:]], source)


def _answer(doc, overrides=None):
    """A correct answer built from the manifest (what a perfect model would say)."""
    answer = {}
    for name, value in NDA["fields"].items():
        quote = NDA["evidence"].get(name)
        page = next(
            (p.number for p in doc.pages if quote and normalize(quote) in p.norm_text), None
        )
        answer[name] = {"value": value, "page": page, "quote": quote, "confidence": 0.95}
    for name, patch in (overrides or {}).items():
        answer[name] = answer[name] | patch
    return cited_model(NdaFields).model_validate(answer, context={"page_count": doc.page_count})


def _by_name(checks):
    return {c.name: c for c in checks}


def test_a_correct_answer_verifies_every_field():
    doc = _nda_doc()
    checks = _by_name(check_fields(doc, _answer(doc), DocType.NDA))

    for name, value in NDA["fields"].items():
        check = checks[name]
        if value is None:
            assert check.status == "absent" and not check.flags, name
        else:
            assert check.status == "verified" and check.match_score == 100, name
            assert check.value_in_quote in (True, None), name
            assert check.block_ids, name


def test_an_invented_quote_is_unverified():
    doc = _nda_doc()
    fake = "This Agreement shall be governed by the laws of the Cayman Islands."
    result = _answer(doc, {"governing_law": {"value": "Cayman Islands", "quote": fake}})

    assert _by_name(check_fields(doc, result, DocType.NDA))["governing_law"].status == "unverified"


def test_a_real_quote_with_an_invented_value_is_caught():
    doc = _nda_doc()
    result = _answer(doc, {"term_months": {"value": NDA["fields"]["term_months"] + 1}})

    check = _by_name(check_fields(doc, result, DocType.NDA))["term_months"]
    assert check.status == "verified"
    assert check.value_in_quote is False


def test_a_quote_on_the_wrong_page_is_found_and_the_page_corrected():
    doc = _nda_doc()
    right = _answer(doc).governing_law.page
    wrong = 1 if right == 2 else 2
    result = _answer(doc, {"governing_law": {"page": wrong}})

    check = _by_name(check_fields(doc, result, DocType.NDA))["governing_law"]
    assert check.status == "verified"
    assert check.page == right
    assert "page_mismatch" in check.flags


def test_text_layer_needs_an_exact_match_but_ocr_allows_small_errors():
    sentence = "This Agreement shall be governed by the laws of the State of Delaware."
    garbled = "This Agreernent shall be govemed by the laws of the State of Delaware."

    assert not find_span(_doc([[garbled]]), sentence, 1).found
    ocr = find_span(_doc([[garbled]], source="ocr"), sentence, 1)
    assert ocr.found and 90 <= ocr.score < 100 and ocr.source == "ocr"


def test_ocr_cannot_rescue_a_quote_that_is_not_there():
    doc = _doc([["The Company shall pay all fees within thirty days."]], source="ocr")
    assert not find_span(doc, "governed by the laws of the State of Delaware", 1).found


def test_quotes_are_unescaped_before_matching():
    # The prompt shows < and > as ‹ and ›, so the model quotes them that way.
    text = "Notices shall be sent to the Company <legal@bishop.example> within 10 days."
    doc = _doc([[text]])

    span = find_span(doc, "sent to the Company ‹legal@bishop.example› within 10 days", 1)

    assert span.found and span.score == 100


def test_whitespace_and_curly_quotes_do_not_matter():
    doc = _doc([['the "Agreement"   is  entered into']])
    assert find_span(doc, "the “Agreement” is entered\ninto", 1).found


def test_a_quote_spanning_two_blocks_points_at_both():
    doc = _doc([["Within ten (10) days after a written request,", "Recipient shall return"]])
    span = find_span(doc, "written request, Recipient shall", 1)
    assert span.found and span.block_ids == [1, 2]


@pytest.mark.parametrize(
    ("name", "value", "quote", "ok"),
    [
        ("term_months", 24, "for two (2) years from the Effective Date", True),
        ("term_months", 18, "for eighteen (18) months", True),
        ("term_months", 18, "for two (2) years", False),
        ("return_destroy_days", 10, "Within ten (10) days after a request", True),
        ("return_destroy_days", 1, "Within ten (10) days after a request", False),
        ("commitment_usd", 1250000, "a commitment of $1,250,000.00", True),
        ("commitment_usd", 250000, "a commitment of $1,250,000.00", False),
        ("effective_date", date(2025, 12, 11), "as of December 11, 2025 (the", True),
        ("effective_date", date(2025, 12, 11), "dated 11th December 2025", True),
        ("effective_date", date(2025, 12, 12), "as of December 11, 2025 (the", False),
        ("governing_law", "Delaware", "laws of the State of Delaware, without", True),
        ("governing_law", "New York", "laws of the State of Delaware, without", False),
        ("management_fee_pct", 1.25, "a management fee of 1.25% per annum", True),
        ("mutual", True, "each Party may furnish the other Party", None),
    ],
)
def test_value_in_quote(name, value, quote, ok):
    assert value_in_quote(name, value, quote, ocr=False) is ok


def test_number_words():
    assert [number_words(n) for n in (7, 24, 60, 100, 115)] == [
        "seven",
        "twenty-four",
        "sixty",
        "one hundred",
        "one hundred fifteen",
    ]


def test_required_field_left_null_is_missing():
    doc = _nda_doc()
    result = _answer(doc, {"counterparty": {"value": None}})
    assert _by_name(check_fields(doc, result, DocType.NDA))["counterparty"].status == "missing"


def test_absent_clause_mentioned_in_the_text_is_flagged():
    doc = _nda_doc()
    doc.pages[1].blocks.append(
        BlockText(99, "Recipient shall not solicit any employee.", "text_layer")
    )
    result = _answer(doc, {"non_solicit_months": {"value": None}})

    check = _by_name(check_fields(doc, result, DocType.NDA))["non_solicit_months"]

    assert check.status == "absent"
    assert check.flags == ["absent_but_mentioned"]
    assert check.keyword == "solicit"


def test_classification_quote_is_checked():
    doc = _doc([["Mutual Confidentiality Agreement", "body"]])
    good = Classification(
        doc_type="nda",
        subtype=None,
        confidence=0.9,
        page=1,
        quote="Mutual Confidentiality Agreement",
    )
    bad = good.model_copy(update={"quote": "Non-Disclosure Agreement"})

    assert check_classification(doc, good).status == "verified"
    assert check_classification(doc, bad).status == "unverified"


def test_nda_signals_need_more_than_a_confidentiality_paragraph():
    side_letter = _doc([["The Investor shall keep the terms of this Side Letter confidential."]])
    nda = _doc(
        [
            [
                '"Confidential Information" means all non-public information.',
                "Recipient shall return or destroy all Confidential Information.",
            ]
        ]
    )

    assert nda_signals(side_letter) == []
    assert nda_signals(nda) == ["defines_confidential_information", "return_or_destroy"]
