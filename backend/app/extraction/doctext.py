"""A parsed document as text, page by page, plus the one normalisation used everywhere.

The prompt is built from this, and every quote the model returns is checked against it, so the
model and the checker always see the same text.
"""

import re
import unicodedata
import uuid
from dataclasses import dataclass, field
from functools import cached_property

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import BlockSource, Document, TextBlock

# The prompt replaces < and > in document text with these look-alikes, so a document can't close
# or fake the <document> tag around it (see prompts.py). Quotes come back with them, so they are
# turned back into < and > before any comparison.
ESCAPES = {"<": "‹", ">": "›"}  # ‹ ›
UNESCAPES = {v: k for k, v in ESCAPES.items()}

_TRANSLATE = str.maketrans(
    {
        **UNESCAPES,
        "‘": "'",
        "’": "'",
        "“": '"',
        "”": '"',
        "‐": "-",
        "‑": "-",
        "‒": "-",
        "–": "-",
        "—": "-",
        " ": " ",
    }
)
_SPACE = re.compile(r"\s+")
# Docling sometimes extracts "<notices@x>" as "< notices@x>"; spacing just inside angle brackets
# doesn't matter for matching.
_INSIDE_BRACKETS = re.compile(r"(?<=<)\s+|\s+(?=>)")


def normalize(text: str) -> str:
    """Unescape ‹ ›, NFKC (ligatures, full-width), straight quotes and dashes, one space, no space
    just inside < >."""
    text = unicodedata.normalize("NFKC", text).translate(_TRANSLATE)
    return _INSIDE_BRACKETS.sub("", _SPACE.sub(" ", text)).strip()


def escape(text: str) -> str:
    return text.replace("<", ESCAPES["<"]).replace(">", ESCAPES[">"])


@dataclass(frozen=True)
class BlockText:
    id: int
    text: str
    source: str
    ocr_confidence: float | None = None


@dataclass
class PageText:
    number: int  # 1-based
    blocks: list[BlockText] = field(default_factory=list)

    @property
    def source(self) -> str:
        """ "ocr" if any block on the page was read by OCR (pages are all one or the other)."""
        if any(b.source == BlockSource.OCR for b in self.blocks):
            return BlockSource.OCR
        return BlockSource.TEXT_LAYER

    @property
    def raw_text(self) -> str:
        """What the model sees for this page: one block per line."""
        return "\n".join(b.text for b in self.blocks)

    @cached_property
    def norm_text(self) -> str:
        return " ".join(normalize(b.text) for b in self.blocks)

    @cached_property
    def block_spans(self) -> list[tuple[int, int, int]]:
        """(start, end, block id) of each block inside norm_text."""
        spans, pos = [], 0
        for b in self.blocks:
            length = len(normalize(b.text))
            spans.append((pos, pos + length, b.id))
            pos += length + 1
        return spans

    def blocks_between(self, start: int, end: int) -> list[int]:
        return [bid for s, e, bid in self.block_spans if s < end and e > start]


@dataclass
class DocText:
    page_count: int
    pages: list[PageText]
    partial: bool = False  # the parser couldn't read every page

    def page(self, number: int) -> PageText | None:
        return self.pages[number - 1] if 1 <= number <= len(self.pages) else None

    @cached_property
    def norm_text(self) -> str:
        return " ".join(p.norm_text for p in self.pages)

    @property
    def is_scanned(self) -> bool:
        return any(p.source == BlockSource.OCR for p in self.pages)


def doc_text_from_blocks(page_count: int, blocks: list[BlockText | tuple], partial=False):
    """Build a DocText from (page, BlockText) pairs. Used by load_doc_text and by tests."""
    pages = [PageText(number=n) for n in range(1, page_count + 1)]
    for page, block in blocks:
        pages[page - 1].blocks.append(block)
    return DocText(page_count=page_count, pages=pages, partial=partial)


def load_doc_text(session: Session, document_id: uuid.UUID) -> DocText:
    doc = session.get_one(Document, document_id)
    rows = session.scalars(
        select(TextBlock)
        .where(TextBlock.document_id == document_id)
        .order_by(TextBlock.page, TextBlock.seq)
    ).all()
    page_count = max([doc.page_count or 0, *(r.page for r in rows)])
    return doc_text_from_blocks(
        page_count,
        [(r.page, BlockText(r.id, r.text, r.source, r.ocr_confidence)) for r in rows],
        partial=(doc.error or "").startswith("partial"),
    )
