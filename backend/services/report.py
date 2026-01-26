from __future__ import annotations

from io import BytesIO
from typing import List, Tuple

from reportlab.lib.utils import ImageReader

try:
    from pypdf import PdfReader, PdfWriter
except Exception:  # pragma: no cover - optional dependency
    PdfReader = None  # type: ignore
    PdfWriter = None  # type: ignore

from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from ..models.schemas import Overlay, OverlayMark, GradeResult


def build_overlay_basic(result: GradeResult) -> Overlay:
    marks: List[OverlayMark] = []
    y = 720.0
    for idx, item in enumerate(result.items, start=1):
        label = "✓" if item.score >= item.max_score * 0.5 else "✗"
        marks.append(OverlayMark(tool="bubble", coords=[40.0, y], text=f"{item.score:.0f}/{item.max_score:.0f}"))
        marks.append(OverlayMark(tool="note", coords=[90.0, y], text=f"Q{item.question_id}: {label} {item.rationale}"))
        y -= 28.0
    if result.needs_review:
        marks.append(OverlayMark(tool="highlight", coords=[36.0, 40.0, 540.0, 20.0], text="Needs review"))
    return Overlay(page=1, marks=marks)


def flatten_to_pdf(summary_text: str, overlay: Overlay) -> bytes:
    """
    Minimal placeholder PDF summarizing grades and overlay notes.
    This does NOT draw over the original submission; it produces a summary page.
    """
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    width, height = letter
    c.setTitle("Graded Summary")

    c.setFont("Helvetica-Bold", 16)
    c.drawString(36, height - 48, "Graded Summary")

    c.setFont("Helvetica", 10)
    y = height - 72
    for line in summary_text.splitlines():
        c.drawString(36, y, line[:1000])
        y -= 14
        if y < 60:
            c.showPage()
            y = height - 36

    # Overlay marks list
    c.showPage()
    c.setFont("Helvetica-Bold", 12)
    c.drawString(36, height - 48, "Overlay Marks")
    c.setFont("Helvetica", 10)
    y = height - 72
    for m in overlay.marks:
        txt = f"{m.tool} @ {m.coords} : {m.text or ''}"
        c.drawString(36, y, txt[:1000])
        y -= 14
        if y < 60:
            c.showPage()
            y = height - 36

    c.save()
    return buf.getvalue()


def get_page_sizes(original_bytes: bytes, mime_type: str | None) -> List[Tuple[float, float]]:
    if (mime_type or "").lower().endswith("pdf") or original_bytes.startswith(b"%PDF"):
        if PdfReader is None:
            raise RuntimeError("pypdf is required for PDF overlays")
        reader = PdfReader(BytesIO(original_bytes))
        sizes = []
        for page in reader.pages:
            box = page.mediabox
            sizes.append((float(box.width), float(box.height)))
        return sizes

    img = ImageReader(BytesIO(original_bytes))
    width, height = img.getSize()
    return [(float(width), float(height))]


def _draw_marks(c: canvas.Canvas, overlay: Overlay) -> None:
    for mark in overlay.marks:
        x, y = mark.coords[:2]
        if mark.tool == "check":
            c.setFont("Helvetica-Bold", 18)
            c.drawString(x, y, "✓")
        elif mark.tool == "cross":
            c.setFont("Helvetica-Bold", 18)
            c.drawString(x, y, "✗")
        elif mark.tool == "note":
            c.setFont("Helvetica-Bold", 12)
            c.drawString(x, y, mark.text or "")
        elif mark.tool == "bubble":
            c.setFont("Helvetica-Bold", 12)
            c.drawString(x, y, mark.text or "")
        elif mark.tool == "highlight" and len(mark.coords) >= 4:
            _, _, w, h = mark.coords[:4]
            c.setFillColorRGB(1, 1, 0)
            c.rect(x, y, w, h, stroke=0, fill=1)
            c.setFillColorRGB(0, 0, 0)


def _overlay_pdf_bytes(page_width: float, page_height: float, overlay: Overlay) -> bytes:
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=(page_width, page_height))
    _draw_marks(c, overlay)
    c.save()
    return buf.getvalue()


def render_marked_pdf(
    original_bytes: bytes,
    mime_type: str | None,
    overlay: Overlay,
) -> bytes:
    if (mime_type or "").lower().endswith("pdf") or original_bytes.startswith(b"%PDF"):
        if PdfReader is None or PdfWriter is None:
            raise RuntimeError("pypdf is required for PDF overlays")
        reader = PdfReader(BytesIO(original_bytes))
        writer = PdfWriter()
        for page_index, page in enumerate(reader.pages):
            if page_index == 0:
                page_width = float(page.mediabox.width)
                page_height = float(page.mediabox.height)
                overlay_bytes = _overlay_pdf_bytes(page_width, page_height, overlay)
                overlay_reader = PdfReader(BytesIO(overlay_bytes))
                page.merge_page(overlay_reader.pages[0])
            writer.add_page(page)
        output = BytesIO()
        writer.write(output)
        return output.getvalue()

    img = ImageReader(BytesIO(original_bytes))
    width, height = img.getSize()
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=(width, height))
    c.drawImage(img, 0, 0, width=width, height=height)
    _draw_marks(c, overlay)
    c.save()
    return buf.getvalue()
