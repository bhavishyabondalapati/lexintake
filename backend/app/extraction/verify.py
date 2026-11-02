"""Check what the model said against the parsed text. Nothing the model returns is trusted until it
passes here.

For each field with a value:
1. the quote must exist in the document: exactly on text-layer pages, fuzzily on OCR pages
   (OCR text has recognition errors, so an exact match would reject correct quotes);
2. the value must appear in its own quote (a real quote next to an invented value is caught here).

For NDA clauses the model calls absent (null), there is no quote to check, so a keyword scan looks
for the clause anyway. And a document that reads like an NDA but wasn't classified as one is
flagged, so an NDA can't slip past the extraction step by being misclassified.
"""

import re
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any

from rapidfuzz import fuzz

from app.extraction.doctext import DocText, normalize
from app.extraction.schemas import Cited, CitedFields, Classification, DocType, is_optional
from app.models import BlockSource

OCR_MATCH_MIN = 90  # an OCR quote at or above this partial_ratio counts as found
OCR_TRUST_MIN = 95  # ...and at or above this it is trusted without review (see route.py)
SHORT_QUOTE = 12  # characters; shorter quotes must match exactly (fuzzy is meaningless on them)

# Keywords that mean an NDA clause is there. If the model says the clause is absent but one of
# these is in the text, a person should look.
ABSENCE_KEYWORDS: dict[str, re.Pattern[str]] = {
    "non_solicit_months": re.compile(r"solicit", re.I),
    "non_compete_months": re.compile(r"non-?compet|not\s+(?:to\s+)?compete|competes\s+with", re.I),
    "residuals_clause": re.compile(r"residuals?\b|unaided\s+memory", re.I),
    "return_destroy_days": re.compile(
        r"return\s+or\s+destroy|destroy\s+or\s+return|return\s+and\s+destroy|destruction\s+of",
        re.I,
    ),
}

# Signs that a document is an NDA. Two or more different ones and a non-NDA classification ->
# needs_review. One isn't enough: side letters often have a short confidentiality paragraph.
NDA_SIGNALS: dict[str, re.Pattern[str]] = {
    "defines_confidential_information": re.compile(
        r"\"?confidential information\"?\s+(?:means|shall\s+mean|includes)", re.I
    ),
    "non_disclosure": re.compile(r"non-?disclosure", re.I),
    "confidentiality_agreement": re.compile(
        r"confidentiality\s+(?:and\s+non-?disclosure\s+)?(?:agreement|undertaking)", re.I
    ),
    "evaluation_material": re.compile(r"evaluation\s+material", re.I),
    "return_or_destroy": ABSENCE_KEYWORDS["return_destroy_days"],
    "use_solely_to_evaluate": re.compile(r"solely\s+for\s+the\s+purpose\s+of\s+evaluating", re.I),
}
NDA_SIGNALS_MIN = 2


@dataclass
class SpanMatch:
    found: bool
    page: int | None = None  # where it was found
    score: float = 0.0  # 100 = exact
    source: str | None = None  # text_layer | ocr, of the page it was found on
    block_ids: list[int] = field(default_factory=list)


@dataclass
class FieldCheck:
    name: str
    value: Any
    page: int | None
    quote: str | None
    confidence: float
    # verified | unverified (quote not found) | absent (null, clause may be absent) |
    # missing (null, but the field should always be there)
    status: str
    match_score: float = 0.0
    source: str | None = None
    block_ids: list[int] = field(default_factory=list)
    value_in_quote: bool | None = None  # None = can't be checked (yes/no fields)
    flags: list[str] = field(default_factory=list)
    keyword: str | None = None  # the keyword that set absent_but_mentioned

    def to_json(self) -> dict[str, Any]:
        out = asdict(self)
        out.pop("name")
        return out


def find_span(doc: DocText, quote: str | None, cited_page: int | None) -> SpanMatch:
    """Look for the quote on the cited page first, then on every other page."""
    q = normalize(quote or "")
    if not q:
        return SpanMatch(found=False)
    order = [cited_page] if cited_page and doc.page(cited_page) else []
    order += [p.number for p in doc.pages if p.number != cited_page]

    best = SpanMatch(found=False)
    for number in order:
        page = doc.page(number)
        assert page is not None
        start = page.norm_text.find(q)
        if start >= 0:
            return SpanMatch(
                True, number, 100.0, page.source, page.blocks_between(start, start + len(q))
            )
        if page.source != BlockSource.OCR or len(q) < SHORT_QUOTE:
            continue
        hit = fuzz.partial_ratio_alignment(q, page.norm_text, processor=str.casefold)
        if hit and hit.score > best.score:
            best = SpanMatch(
                hit.score >= OCR_MATCH_MIN,
                number,
                round(hit.score, 1),
                page.source,
                page.blocks_between(hit.dest_start, hit.dest_end),
            )
    return best if best.found else SpanMatch(False, score=best.score)


# --- Is the value in its own quote? -------------------------------------------------------------

_ONES = (
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen "
    "fifteen sixteen seventeen eighteen nineteen"
).split()
_TENS = "_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()
_MONTHS = (
    "january february march april may june july august september october november december"
).split()


def number_words(n: int) -> str | None:
    if 0 <= n < 20:
        return _ONES[n]
    if 20 <= n < 100:
        return _TENS[n // 10] + (f"-{_ONES[n % 10]}" if n % 10 else "")
    if 100 <= n < 1000:
        rest = number_words(n % 100) if n % 100 else None
        return f"{_ONES[n // 100]} hundred" + (f" {rest}" if rest else "")
    return None


def _number_forms(n: int) -> list[str]:
    forms = [str(n)]
    if words := number_words(n):
        forms.append(words)
    return forms


def _int_forms(name: str, n: int) -> list[str]:
    forms = _number_forms(n)
    if name.endswith("_months") and n % 12 == 0:  # "two (2) years" supports 24 months
        forms += _number_forms(n // 12)
    return forms


def _date_forms(d: date) -> list[str]:
    month = _MONTHS[d.month - 1]
    return [
        d.isoformat(),
        f"{month} {d.day} {d.year}",
        f"{month[:3]} {d.day} {d.year}",
        f"{d.day} {month} {d.year}",
        f"{d.month}/{d.day}/{d.year}",
        f"{d.month:02d}/{d.day:02d}/{d.year}",
    ]


def _simplify(text: str) -> str:
    """Lower case, no commas or full stops after month abbreviations, no 1st/2nd/3rd/4th."""
    text = re.sub(r"(?<=\d),(?=\d{3})", "", normalize(text))  # 1,250,000 -> 1250000
    text = text.casefold().replace(",", " ")
    text = re.sub(r"\b(\d{1,2})(?:st|nd|rd|th)\b", r"\1", text)
    text = re.sub(r"\b([a-z]{3})\.", r"\1", text)
    return re.sub(r"\s+", " ", text)


def value_in_quote(name: str, value: Any, quote: str, ocr: bool) -> bool | None:
    q = _simplify(quote)
    if isinstance(value, bool):
        return None  # a yes/no about the whole clause; the quote is checked, the value can't be
    if isinstance(value, int):
        for form in map(_simplify, _int_forms(name, value)):
            # Digits must not be part of a longer number; words must be whole words.
            pattern = (
                rf"(?<!\d){re.escape(form)}(?!\d)"
                if form[0].isdigit()
                else (rf"\b{re.escape(form)}\b")
            )
            if re.search(pattern, q):
                return True
        return False
    if isinstance(value, float):
        forms = {f"{value:g}", f"{value:.2f}", f"{value:.1f}"}
        return any(f in q for f in forms)
    if isinstance(value, date):
        return any(_simplify(f) in q for f in _date_forms(value))
    if isinstance(value, str):
        v = _simplify(value)
        if v in q:
            return True
        return ocr and fuzz.partial_ratio(v, q) >= OCR_MATCH_MIN
    return None


# --- Whole-result checks ------------------------------------------------------------------------


def check_fields(doc: DocText, result: CitedFields, doc_type: DocType) -> list[FieldCheck]:
    fields_model = type(result).FIELDS
    checks = []
    for name, cited in result.cited().items():
        checks.append(_check_field(doc, name, cited, is_optional(fields_model, name), doc_type))
    return checks


def _check_field(
    doc: DocText, name: str, cited: Cited, optional: bool, doc_type: DocType
) -> FieldCheck:
    check = FieldCheck(name, cited.value, cited.page, cited.quote, cited.confidence, "absent")
    if cited.value is None:
        if not optional:
            check.status = "missing"
        elif doc_type == DocType.NDA and name in ABSENCE_KEYWORDS:
            if m := ABSENCE_KEYWORDS[name].search(doc.norm_text):
                check.flags.append("absent_but_mentioned")
                check.keyword = m.group(0)
        return check

    span = find_span(doc, cited.quote, cited.page)
    check.match_score, check.source, check.block_ids = span.score, span.source, span.block_ids
    if not span.found:
        check.status = "unverified"
        return check
    check.status = "verified"
    if span.page != cited.page:
        check.flags.append("page_mismatch")
        check.page = span.page  # point at where the text really is
    check.value_in_quote = value_in_quote(
        name, cited.value, cited.quote or "", ocr=span.source == BlockSource.OCR
    )
    return check


def check_classification(doc: DocText, c: Classification) -> FieldCheck:
    span = find_span(doc, c.quote, c.page)
    check = FieldCheck(
        "classification",
        c.doc_type.value,
        span.page or c.page,
        c.quote,
        c.confidence,
        "verified" if span.found else "unverified",
        span.score,
        span.source,
        span.block_ids,
    )
    if span.found and span.page != c.page:
        check.flags.append("page_mismatch")
    return check


def nda_signals(doc: DocText) -> list[str]:
    """Which NDA signals the text contains."""
    return [name for name, pattern in NDA_SIGNALS.items() if pattern.search(doc.norm_text)]
