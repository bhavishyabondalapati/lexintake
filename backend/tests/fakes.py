"""Test doubles, so unit tests never load Docling."""

from pathlib import Path

from app.models import BlockSource
from app.parsing.base import Block, ParseResult


class FakeParser:
    """Returns two blocks per page; can be told to fail or to report a partial parse."""

    def __init__(
        self,
        pages: int = 1,
        error: Exception | None = None,
        failed_pages: list[int] | None = None,
    ) -> None:
        self.pages = pages
        self.error = error
        self.failed_pages = failed_pages or []
        self.calls: list[Path] = []

    def parse(self, path: Path) -> ParseResult:
        self.calls.append(path)
        if self.error:
            raise self.error
        blocks = [
            Block(
                page=page,
                seq=i,
                bbox=(72.0, 72.0 + 20 * i, 540.0, 90.0 + 20 * i),
                text=f"page {page} block {i}",
                label="text",
                source=BlockSource.TEXT_LAYER,
            )
            for i, page in enumerate(p for p in range(1, self.pages + 1) for _ in range(2))
        ]
        return ParseResult(page_count=self.pages, blocks=blocks, failed_pages=self.failed_pages)


class RecordingQueue:
    """Stands in for Celery: remembers which documents were queued."""

    def __init__(self, fail: bool = False) -> None:
        self.queued: list = []
        self.fail = fail

    def __call__(self, document_id) -> None:
        if self.fail:
            raise ConnectionError("redis is down")
        self.queued.append(document_id)
