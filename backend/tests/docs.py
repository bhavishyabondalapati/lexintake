"""Put parsed documents into the test database, built from manifest entries (no PDF, no Docling)."""

import json
import uuid

from app.db import session_scope
from app.models import AuditLog, Document, Status, TextBlock
from tests.conftest import FIXTURES

MANIFEST = {
    d["doc_id"]: d for d in json.loads((FIXTURES / "manifest.json").read_text())["documents"]
}
SPECIAL = {
    d["doc_id"]: d
    for d in json.loads((FIXTURES / "special_fixtures.json").read_text())["documents"]
}
INJECTION_NDA = SPECIAL["injection_nda"]  # an NDA with planted prompt-injection attacks
ANGLE_NDA = SPECIAL["angle_brackets_nda"]  # an NDA with a literal <...> in a clause
NDA = MANIFEST["syn_00010"]  # digital, 2 pages
LEGAL_NOTICE = MANIFEST["syn_00001"]  # digital notice of breach
SIDE_LETTER = MANIFEST["syn_00002"]  # scanned

TITLES = {
    "nda": "Confidentiality Agreement",
    "side_letter": "Side Letter",
    "legal_notice": "Notice of Breach",
    "investor_notice": "Capital Call Notice",
    "other": "Newsletter",
}
NDA_DEFINITION = (
    '"Confidential Information" means all non-public information, in any form, furnished in'
    " connection with the Transaction."
)


def add_parsed_doc(
    entry: dict,
    *,
    source: str = "text_layer",
    extra: list[str] = (),
    partial: bool = False,
    status: Status = Status.PARSED,
) -> uuid.UUID:
    """A parsed document over two pages. Its text is the entry's paragraphs when it has them (the
    special fixtures), else a title + the evidence sentences."""
    if "paragraphs" in entry:
        sentences = list(entry["paragraphs"])
        title = []
    else:
        sentences = list(dict.fromkeys(entry["evidence"].values()))
        if entry["doc_type"] == "nda":
            sentences.insert(0, NDA_DEFINITION)
        title = [TITLES[entry["doc_type"]]]
    half = (len(sentences) + 1) // 2
    pages = [[*title, *sentences[:half]], [*sentences[half:], *extra]]
    with session_scope() as session:
        doc = Document(
            sha256=uuid.uuid4().hex + uuid.uuid4().hex,
            filename=f"{entry['doc_id']}.pdf",
            size_bytes=1,
            mime="application/pdf",
            source="upload",
            status=status,
            retry_count=1,
            page_count=len(pages),
            error="partial: pages [2] could not be parsed" if partial else None,
        )
        session.add(doc)
        session.flush()
        session.add(
            AuditLog(
                document_id=doc.id, from_status="parsing", to_status=status, actor="test", detail={}
            )
        )
        seq = 0
        for number, texts in enumerate(pages, 1):
            for text in texts:
                session.add(
                    TextBlock(
                        document_id=doc.id,
                        page=number,
                        seq=seq,
                        x0=72,
                        y0=72 + 20 * seq,
                        x1=540,
                        y1=90 + 20 * seq,
                        text=text,
                        label="text",
                        source=source,
                        ocr_confidence=0.98 if source == "ocr" else None,
                    )
                )
                seq += 1
        return doc.id
