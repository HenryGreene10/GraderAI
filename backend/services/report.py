from __future__ import annotations

import datetime as dt
import logging
from io import BytesIO
from typing import List, Tuple, Optional

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

logger = logging.getLogger(__name__)

MISSING_OVERLAY_BANNER = "NO OVERLAY GENERATED — NEEDS REVIEW"


def build_overlay_basic(result: GradeResult) -> Overlay:
    marks: List[OverlayMark] = []
    y = 720.0
    for idx, item in enumerate(result.items, start=1):
        label = "✓" if item.score >= item.max_score * 0.5 else "✗"
        marks.append(OverlayMark(tool="bubble", coords=[40.0, y], text=f"{item.score:.0f}/{item.max_score:.0f}"))
        marks.append(OverlayMark(tool="note", coords=[90.0, y], text=f"Q{item.question_id}: {label} {item.rationale}"))
        y -= 28.0
    return Overlay(page=1, marks=marks)


def _score_text(total_score: Optional[float], total_max: Optional[float]) -> str:
    if total_max and total_max > 0:
        return f"Score: {float(total_score or 0.0):.0f}/{float(total_max):.0f}"
    return "Score: (unavailable) — NEEDS REVIEW"


def build_minimal_overlay(
    upload_id: str,
    total_score: Optional[float],
    total_max: Optional[float],
) -> Overlay:
    return Overlay(
        page=1,
        marks=[OverlayMark(tool="note", coords=[36.0, 36.0], text=_score_text(total_score, total_max))],
        meta={
            "upload_id": upload_id,
            "generated_at": dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
            "note": "minimal overlay v1",
        },
    )


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


def _overlay_pdf_bytes(
    page_width: float,
    page_height: float,
    overlay: Overlay,
    extra_marks: Optional[List[OverlayMark]] = None,
) -> bytes:
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=(page_width, page_height))
    _draw_marks(c, overlay)
    if extra_marks:
        _draw_marks(c, Overlay(page=overlay.page, marks=extra_marks))
    c.save()
    return buf.getvalue()


def _draw_missing_overlay_banner(c: canvas.Canvas, page_width: float, page_height: float, text: str) -> None:
    banner_height = 28.0
    padding = 18.0
    y = page_height - padding - banner_height
    c.setStrokeColorRGB(0.86, 0.1, 0.1)
    c.setLineWidth(2)
    c.rect(padding, y, page_width - 2 * padding, banner_height, stroke=1, fill=0)
    c.setFillColorRGB(0.86, 0.1, 0.1)
    c.setFont("Helvetica-Bold", 12)
    c.drawString(padding + 8, y + 9, text)


def _banner_overlay_pdf_bytes(page_width: float, page_height: float, text: str) -> bytes:
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=(page_width, page_height))
    _draw_missing_overlay_banner(c, page_width, page_height, text)
    c.save()
    return buf.getvalue()


def render_marked_pdf(
    original_bytes: bytes,
    mime_type: str | None,
    overlay: Optional[Overlay],
    *,
    missing_overlay_text: Optional[str] = None,
    smoke_score_text: Optional[str] = None,
) -> bytes:
    missing = overlay is None
    banner_text = missing_overlay_text or MISSING_OVERLAY_BANNER
    if missing:
        logger.warning("render_marked_pdf missing overlay; stamping banner=%s", banner_text)

    marks = overlay.marks if overlay else []
    mark_count = len(marks)
    first = marks[0] if marks else None
    first_info = f"{first.tool}:{(first.text or '')}" if first else "none"

    if (mime_type or "").lower().endswith("pdf") or original_bytes.startswith(b"%PDF"):
        if PdfReader is None or PdfWriter is None:
            raise RuntimeError("pypdf is required for PDF overlays")
        reader = PdfReader(BytesIO(original_bytes))
        writer = PdfWriter()
        for page_index, page in enumerate(reader.pages):
            if page_index == 0:
                page_width = float(page.mediabox.width)
                page_height = float(page.mediabox.height)
                extra_marks: List[OverlayMark] = []
                if not missing and mark_count == 0:
                    extra_marks.append(
                        OverlayMark(
                            tool="note",
                            coords=[36.0, max(36.0, page_height - 48.0)],
                            text="OVERLAY EMPTY — NEEDS REVIEW",
                        )
                    )
                if smoke_score_text:
                    extra_marks.append(
                        OverlayMark(
                            tool="bubble",
                            coords=[36.0, max(36.0, page_height - 72.0), 260.0, 26.0],
                            text=smoke_score_text,
                        )
                    )
                if missing:
                    overlay_bytes = _banner_overlay_pdf_bytes(page_width, page_height, banner_text)
                else:
                    overlay_bytes = _overlay_pdf_bytes(page_width, page_height, overlay, extra_marks=extra_marks)
                overlay_reader = PdfReader(BytesIO(overlay_bytes))
                page.merge_page(overlay_reader.pages[0])
            writer.add_page(page)
        output = BytesIO()
        writer.write(output)
        logger.info(
            "render_marked_pdf: drew background then %s marks first=%s",
            mark_count,
            first_info,
        )
        return output.getvalue()

    img = ImageReader(BytesIO(original_bytes))
    width, height = img.getSize()
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=(width, height))
    c.drawImage(img, 0, 0, width=width, height=height)
    if missing:
        _draw_missing_overlay_banner(c, width, height, banner_text)
    else:
        _draw_marks(c, overlay)
        if mark_count == 0:
            _draw_marks(
                c,
                Overlay(
                    page=overlay.page,
                    marks=[
                        OverlayMark(
                            tool="note",
                            coords=[36.0, max(36.0, height - 48.0)],
                            text="OVERLAY EMPTY — NEEDS REVIEW",
                        )
                    ],
                ),
            )
        if smoke_score_text:
            _draw_marks(
                c,
                Overlay(
                    page=overlay.page,
                    marks=[
                        OverlayMark(
                            tool="bubble",
                            coords=[36.0, max(36.0, height - 72.0), 260.0, 26.0],
                            text=smoke_score_text,
                        )
                    ],
                ),
            )
    c.save()
    logger.info(
        "render_marked_pdf: drew background then %s marks first=%s",
        mark_count,
        first_info,
    )
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
