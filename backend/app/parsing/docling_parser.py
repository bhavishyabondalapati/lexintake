"""Docling-based parser (worker image only: needs torch and the baked-in models).

OCR is slow and less accurate than the PDF's own text, so it only runs on pages that have no
text layer. We decide that ourselves, per page, with pypdfium2 (Docling's default would also OCR
pictures, such as logos, on pages that do have text):

    every page has text  -> one Docling pass without OCR (fast)
    no page has text     -> one pass with full-page OCR
    a mix                -> both passes, each page taken from the pass that suits it
"""

import logging
import math
import os
from pathlib import Path
from typing import Any

import pypdfium2 as pdfium
from docling.backend.pypdfium2_backend import PyPdfiumDocumentBackend
from docling.datamodel.accelerator_options import AcceleratorOptions
from docling.datamodel.base_models import ConversionStatus, InputFormat
from docling.datamodel.pipeline_options import OcrMode, PdfPipelineOptions, RapidOcrOptions
from docling.document_converter import DocumentConverter, PdfFormatOption
from docling_core.types.doc import DoclingDocument, TableItem

from app.models import BlockSource
from app.parsing.base import Block, ParseError, ParseResult

log = logging.getLogger(__name__)

# A page with fewer visible characters than this is treated as having no text layer.
MIN_TEXT_CHARS = 20


def text_layer_pages(path: Path) -> tuple[int, set[int]]:
    """Return (page count, 1-based numbers of the pages that have a real text layer)."""
    try:
        pdf = pdfium.PdfDocument(path)
    except pdfium.PdfiumError as e:
        raise ParseError(f"cannot open PDF: {e}") from e
    try:
        with_text = set()
        for i in range(len(pdf)):
            text = pdf[i].get_textpage().get_text_range()
            if sum(not c.isspace() for c in text) >= MIN_TEXT_CHARS:
                with_text.add(i + 1)
        return len(pdf), with_text
    finally:
        pdf.close()


def table_text(item: TableItem) -> str:
    """A table as plain text: one line per row, cells separated by ' | '."""
    rows = []
    for row in item.data.grid:
        cells = [cell.text.strip() for cell in row]
        if any(cells):
            rows.append(" | ".join(cells))
    return "\n".join(rows)


def document_blocks(
    doc: DoclingDocument, pages: set[int], source: BlockSource, ocr_scores: dict[int, float]
) -> list[Block]:
    """Turn Docling's items into Blocks, keeping only the given pages.

    `seq` is set to 0 here; the caller numbers blocks once both passes are merged.
    """
    blocks = []
    for item, _level in doc.iterate_items():
        prov = getattr(item, "prov", None)
        if not prov:
            continue
        text = table_text(item) if isinstance(item, TableItem) else getattr(item, "text", "")
        for p in prov:
            if p.page_no not in pages:
                continue
            # An item split across pages has one prov per page, each pointing at its slice.
            part = text[p.charspan[0] : p.charspan[1]] if len(prov) > 1 else text
            if not part.strip():
                continue
            height = doc.pages[p.page_no].size.height
            box = p.bbox.to_top_left_origin(page_height=height)
            score = ocr_scores.get(p.page_no) if source == BlockSource.OCR else None
            blocks.append(
                Block(
                    page=p.page_no,
                    seq=0,
                    bbox=(round(box.l, 2), round(box.t, 2), round(box.r, 2), round(box.b, 2)),
                    text=part.strip(),
                    label=str(item.label.value),
                    source=source,
                    ocr_confidence=score,
                )
            )
    return blocks


class DoclingParser:
    def __init__(
        self,
        device: str = "auto",
        artifacts_path: str | None = None,
        num_threads: int | None = None,
    ) -> None:
        # In the worker image the models are baked in here (and downloads are switched off).
        self.artifacts_path = artifacts_path or os.environ.get("DOCLING_ARTIFACTS_PATH")
        self.accelerator = AcceleratorOptions(
            device=device, num_threads=num_threads or int(os.environ.get("OMP_NUM_THREADS", 4))
        )
        self._converters: dict[bool, DocumentConverter] = {}

    def _converter(self, ocr: bool) -> DocumentConverter:
        if ocr not in self._converters:
            options = PdfPipelineOptions(
                do_ocr=ocr,
                do_table_structure=True,
                accelerator_options=self.accelerator,
                artifacts_path=self.artifacts_path,
            )
            if ocr:
                options.ocr_options = RapidOcrOptions(lang=["iso:en"], mode=OcrMode.FULL_PAGE)
            # The pdfium backend renders the page images the layout model looks at with pdfium's
            # own bundled standard fonts. Docling's default backend (docling-parse) draws text
            # with the system's fonts, and the slim Linux image has none: PDFs that don't embed
            # Helvetica/Times (common) came out as boxes, and the layout model merged whole
            # pages into one block. pdfium gives the same result on a Mac and in Docker.
            converter = DocumentConverter(
                format_options={
                    InputFormat.PDF: PdfFormatOption(
                        pipeline_options=options, backend=PyPdfiumDocumentBackend
                    )
                }
            )
            converter.initialize_pipeline(InputFormat.PDF)  # load the models now, not mid-task
            self._converters[ocr] = converter
        return self._converters[ocr]

    def warm_up(self) -> None:
        self._converter(ocr=False)
        self._converter(ocr=True)

    def _run(self, path: Path, ocr: bool, pages: set[int]) -> tuple[list[Block], list[str], Any]:
        result = self._converter(ocr).convert(path, raises_on_error=False)
        errors = [e.error_message for e in result.errors]
        if result.status not in (ConversionStatus.SUCCESS, ConversionStatus.PARTIAL_SUCCESS):
            raise ParseError(f"Docling {result.status.value}: {'; '.join(errors) or 'no detail'}")
        scores = {}
        if ocr:
            for page_no, conf in result.confidence.pages.items():
                if not math.isnan(conf.ocr_score):
                    scores[page_no] = round(float(conf.ocr_score), 4)
        source = BlockSource.OCR if ocr else BlockSource.TEXT_LAYER
        return document_blocks(result.document, pages, source, scores), errors, result.document

    def parse(self, path: Path) -> ParseResult:
        page_count, text_pages = text_layer_pages(path)
        if page_count == 0:
            raise ParseError("PDF has no pages")
        all_pages = set(range(1, page_count + 1))
        scan_pages = all_pages - text_pages

        blocks: list[Block] = []
        errors: list[str] = []
        converted: set[int] = set()
        for ocr, pages in ((False, text_pages), (True, scan_pages)):
            if not pages:
                continue
            found, errs, doc = self._run(path, ocr, pages)
            blocks += found
            errors += errs
            converted |= set(doc.pages) & pages

        blocks.sort(key=lambda b: b.page)  # stable: keeps Docling's reading order within a page
        numbered = [Block(**{**b.__dict__, "seq": i}) for i, b in enumerate(blocks)]
        return ParseResult(
            page_count=page_count,
            blocks=numbered,
            ocr_pages=sorted(scan_pages),
            failed_pages=sorted(all_pages - converted),
            errors=errors,
        )
