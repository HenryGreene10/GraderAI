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
from .coords import px_to_pdf, rect_px_to_pdf
from .marking import DebugLayout


def build_overlay_basic(result: GradeResult) -> Overlay:
    marks: List[OverlayMark] = []
    y = 720.0
    for idx, item in enumerate(result.items, start=1):
        label = "✓" if item.score >= item.max_score * 0.5 else "✗"
        marks.append(OverlayMark(tool="bubble", coords=[40.0, y], text=f"{item.score:.0f}/{item.max_score:.0f}"))
        marks.append(OverlayMark(tool="note", coords=[90.0, y], text=f"Q{item.question_id}: {label} {item.rationale}"))
        y -= 28.0
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
            if len(mark.coords) >= 4:
                _, _, w, h = mark.coords[:4]
                c.setFillColorRGB(1, 1, 1)
                c.rect(x, y, w, h, stroke=1, fill=1)
                c.setFillColorRGB(0, 0, 0)
                c.drawString(x + 6, y + max(6, (h - 12) / 2), mark.text or "")
            else:
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


def render_debug_layout_pdf(
    original_bytes: bytes,
    mime_type: str | None,
    ocr_boxes: object,
    debug_layout: DebugLayout,
    normalized_size: Tuple[float, float],
) -> bytes:
    if PdfReader is None or PdfWriter is None:
        raise RuntimeError("pypdf is required for debug overlays")

    reader = PdfReader(BytesIO(original_bytes))
    writer = PdfWriter()
    page = reader.pages[0]
    page_width = float(page.mediabox.width)
    page_height = float(page.mediabox.height)

    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=(page_width, page_height))

    norm_w, norm_h = normalized_size
    if norm_w <= 0 or norm_h <= 0:
        # Best-effort infer from OCR boxes if present
        analyze = (ocr_boxes or {}).get("analyzeResult", {}) if isinstance(ocr_boxes, dict) else {}
        read_results = analyze.get("readResults") or []
        if read_results:
            norm_w = float(read_results[0].get("width") or 0.0)
            norm_h = float(read_results[0].get("height") or 0.0)
    if norm_w <= 0 or norm_h <= 0:
        norm_w, norm_h = page_width, page_height

    c.setStrokeColorRGB(0.1, 0.4, 0.8)
    for rect in debug_layout.boxes_px:
        x, y, w, h = rect_px_to_pdf(rect, (norm_w, norm_h), (page_width, page_height))
        c.rect(x, y, w, h, stroke=1, fill=0)

    c.setFillColorRGB(0.9, 0.1, 0.1)
    c.setFont("Helvetica", 8)
    for anchor in debug_layout.anchors:
        ax, ay = px_to_pdf(anchor.anchor_px[0], anchor.anchor_px[1], (norm_w, norm_h), (page_width, page_height))
        c.circle(ax, ay, 3, stroke=0, fill=1)
        c.drawString(ax + 4, ay + 2, f"Q{anchor.question_id} ({anchor.source})")

    c.save()
    overlay_reader = PdfReader(BytesIO(buf.getvalue()))
    page.merge_page(overlay_reader.pages[0])
    writer.add_page(page)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()
