"""What every parser returns. The worker only knows this interface, so tests can use a fake
parser and never load the (slow, heavy) Docling models."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from app.models import BlockSource


@dataclass(frozen=True)
class Block:
    page: int  # 1-based
    seq: int  # reading order within the document
    bbox: tuple[float, float, float, float]  # x0, y0, x1, y1 in points, top-left origin
    text: str
    label: str
    source: BlockSource
    ocr_confidence: float | None = None


@dataclass
class ParseResult:
    page_count: int
    blocks: list[Block]
    ocr_pages: list[int] = field(default_factory=list)
    failed_pages: list[int] = field(default_factory=list)  # pages Docling could not convert
    errors: list[str] = field(default_factory=list)  # non-fatal problems Docling reported


class ParseError(Exception):
    """The file itself can't be parsed (corrupt, encrypted, ...). Retrying won't help."""


class Parser(Protocol):
    def parse(self, path: Path) -> ParseResult: ...
