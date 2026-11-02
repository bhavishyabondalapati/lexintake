"""Routing: extracted or needs_review.

A document is `extracted` only when there is no reason at all to send it to a person. The model's
confidence is one input, never the only one: a confident answer whose quote can't be found still
goes to review, and so does a verified answer the model itself wasn't sure about.
"""

from dataclasses import asdict, dataclass

from app.config import Settings
from app.extraction.doctext import DocText
from app.extraction.schemas import Classification, DocType
from app.extraction.verify import NDA_SIGNALS_MIN, OCR_TRUST_MIN, FieldCheck
from app.models import BlockSource


@dataclass(frozen=True)
class Reason:
    code: str
    field: str | None = None
    detail: str = ""

    def to_json(self) -> dict:
        return asdict(self)


def document_reasons(doc: DocText, injection: list[str]) -> list[Reason]:
    reasons = []
    if doc.partial:
        reasons.append(Reason("partial_parse", detail="some pages could not be parsed"))
    if not doc.norm_text:
        reasons.append(Reason("no_text", detail="the parser found no text"))
    if injection:
        # The text talks to an AI system. Whatever the model answered, a person should look.
        reasons.append(Reason("suspected_prompt_injection", detail=", ".join(injection)))
    return reasons


def classification_reasons(
    settings: Settings, c: Classification, check: FieldCheck, signals: list[str]
) -> list[Reason]:
    """Any of these means the type itself is in doubt, so step 2 (extraction) is skipped."""
    reasons = []
    if c.confidence < settings.classify_min_confidence:
        reasons.append(
            Reason(
                "low_classification_confidence",
                detail=f"{c.confidence:.2f} < {settings.classify_min_confidence}",
            )
        )
    if check.status != "verified":
        reasons.append(Reason("classification_unverified", detail="quote not found in the text"))
    if c.doc_type != DocType.NDA and len(signals) >= NDA_SIGNALS_MIN:
        # The NDA guard: an NDA misclassified as something else would skip the NDA checks.
        reasons.append(Reason("nda_signals_but_not_nda", detail=", ".join(signals)))
    return reasons


def scope_reasons(c: Classification) -> list[Reason]:
    if c.doc_type == DocType.OTHER:
        return [Reason("out_of_scope", detail=f"other ({c.subtype})")]
    return []


def field_reasons(settings: Settings, checks: list[FieldCheck]) -> list[Reason]:
    reasons = []
    for c in checks:
        ocr = c.source == BlockSource.OCR
        min_conf = settings.ocr_field_min_confidence if ocr else settings.field_min_confidence
        if c.status == "unverified":
            reasons.append(Reason("field_unverified", c.name, "quote not found in the text"))
        elif c.status == "missing":
            reasons.append(Reason("field_missing", c.name, "required field left empty"))
        elif c.status == "verified":
            if c.value_in_quote is False:
                reasons.append(Reason("value_not_in_quote", c.name, f"{c.value} not in quote"))
            if ocr and c.match_score < OCR_TRUST_MIN:
                reasons.append(
                    Reason(
                        "ocr_match_below_trust", c.name, f"match {c.match_score} < {OCR_TRUST_MIN}"
                    )
                )
        if c.status != "missing" and c.confidence < min_conf:
            suffix = " (OCR)" if ocr else ""
            reasons.append(
                Reason("low_field_confidence", c.name, f"{c.confidence:.2f} < {min_conf}{suffix}")
            )
        if "absent_but_mentioned" in c.flags:
            reasons.append(
                Reason("absent_but_mentioned", c.name, f"model says absent; text has {c.keyword!r}")
            )
    return reasons
