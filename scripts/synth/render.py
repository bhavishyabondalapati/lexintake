"""Render a Document to a digital PDF, and turn a digital PDF into a scanned-looking one.

Both outputs are byte-for-byte reproducible: ReportLab runs in invariant mode (fixed creation date
and document ID), and the scanned version uses a seeded NumPy generator and fixed PDF dates.
"""

import io
import time
from xml.sax.saxutils import escape

import numpy as np
import pypdfium2 as pdfium
from PIL import Image, ImageFilter
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate

from scripts.synth.templates import Document

SCAN_DPI = 150
# Pillow would otherwise stamp the current time into the PDF.
FIXED_PDF_DATE = time.strptime("2025-01-01", "%Y-%m-%d")

_BODY = ParagraphStyle("body", fontName="Helvetica", fontSize=10.5, leading=14.5, spaceAfter=8)
STYLES = {
    "body": _BODY,
    "title": ParagraphStyle(
        "title",
        parent=_BODY,
        fontName="Helvetica-Bold",
        fontSize=14,
        leading=18,
        alignment=TA_CENTER,
        spaceAfter=14,
    ),
    "heading": ParagraphStyle(
        "heading", parent=_BODY, fontName="Helvetica-Bold", fontSize=11.5, spaceBefore=4
    ),
    "line": ParagraphStyle("line", parent=_BODY, spaceAfter=0),
    "small": ParagraphStyle("small", parent=_BODY, spaceAfter=12),
}


def _footer(canvas, doc) -> None:
    canvas.setFont("Helvetica", 8)
    canvas.drawCentredString(LETTER[0] / 2, 36, f"Page {doc.page}")


def render_pdf(document: Document) -> bytes:
    """A digital PDF with a real text layer."""
    buffer = io.BytesIO()
    template = SimpleDocTemplate(
        buffer,
        pagesize=LETTER,
        leftMargin=72,
        rightMargin=72,
        topMargin=72,
        bottomMargin=72,
        title=document.title,
        invariant=1,  # fixed creation date and document ID, so output is reproducible
    )
    # KeepTogether stops a paragraph splitting across pages, so the page footer can never land in
    # the middle of an evidence sentence in the extracted text. Runs of "line" blocks (address and
    # signature lines) are also kept together with the block that follows them.
    story, group = [], []
    for block in document.blocks:
        markup = escape(block.text)
        if block.heading:
            markup = f"<b>{escape(block.heading)}</b> {markup}"
        group.append(Paragraph(markup, STYLES[block.style]))
        if block.style != "line":
            story.append(KeepTogether(group))
            group = []
    if group:
        story.append(KeepTogether(group))
    template.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return buffer.getvalue()


def scan_pdf(pdf_bytes: bytes, rng: np.random.Generator, rotation_deg: float) -> bytes:
    """Rasterise each page and make it look scanned. The result has no text layer."""
    pdf = pdfium.PdfDocument(pdf_bytes)
    pages = []
    try:
        for page in pdf:
            image = page.render(scale=SCAN_DPI / 72, grayscale=True).to_pil().convert("L")
            image = image.rotate(rotation_deg, resample=Image.BICUBIC, fillcolor=255)
            image = image.filter(ImageFilter.GaussianBlur(0.7))
            pixels = np.asarray(image, dtype=np.float32)
            pixels = 18 + pixels * 0.9  # lower the contrast: grey ink on off-white paper
            pixels += rng.normal(0, 7, pixels.shape)  # sensor grain
            specks = rng.random(pixels.shape) < 0.0006  # dust
            pixels[specks] = rng.uniform(0, 90, int(specks.sum()))
            pages.append(Image.fromarray(np.clip(pixels, 0, 255).astype(np.uint8), "L"))
    finally:
        pdf.close()

    buffer = io.BytesIO()
    pages[0].save(
        buffer,
        "PDF",
        save_all=True,
        append_images=pages[1:],
        resolution=SCAN_DPI,
        creationDate=FIXED_PDF_DATE,
        modDate=FIXED_PDF_DATE,
    )
    return buffer.getvalue()


def page_count(pdf_bytes: bytes) -> int:
    pdf = pdfium.PdfDocument(pdf_bytes)
    try:
        return len(pdf)
    finally:
        pdf.close()


def pdf_text(pdf_bytes: bytes) -> str:
    """All text in the PDF's text layer (empty for scanned PDFs)."""
    pdf = pdfium.PdfDocument(pdf_bytes)
    try:
        return "\n".join(page.get_textpage().get_text_range() for page in pdf)
    finally:
        pdf.close()
