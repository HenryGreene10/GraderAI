from __future__ import annotations

import datetime as dt
import logging
import os
from io import BytesIO
from typing import Any, Dict, List, Tuple, Optional

from reportlab.lib.utils import ImageReader

try:
    from pypdf import PdfReader, PdfWriter
except Exception:  # pragma: no cover - optional dependency
    PdfReader = None  # type: ignore
    PdfWriter = None  # type: ignore

from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from ..models.schemas import Overlay, OverlayMark, GradeResult
from .marking import DebugLayout

logger = logging.getLogger(__name__)

MISSING_OVERLAY_BANNER = "NO OVERLAY GENERATED — NEEDS REVIEW"
MARK_DRAW_SIZE_PT = float(os.getenv("OVERLAY_MARK_SIZE_PT", "22"))
MARK_DRAW_STROKE_PT = float(os.getenv("OVERLAY_MARK_STROKE_PT", "2.2"))
NOTE_FONT_SIZE_PT = float(os.getenv("OVERLAY_NOTE_FONT_PT", "13"))
BUBBLE_FONT_SIZE_PT = float(os.getenv("OVERLAY_BUBBLE_FONT_PT", "13"))


def _draw_vector_check(c: canvas.Canvas, x: float, y: float, size: float = MARK_DRAW_SIZE_PT) -> None:
    c.setStrokeColorRGB(0.0, 0.55, 0.12)
    c.setLineWidth(MARK_DRAW_STROKE_PT)
    try:
        c.setLineCap(1)
    except Exception:
        pass
    c.line(x, y + size * 0.35, x + size * 0.28, y)
    c.line(x + size * 0.28, y, x + size, y + size * 0.92)


def _draw_vector_cross(c: canvas.Canvas, x: float, y: float, size: float = MARK_DRAW_SIZE_PT) -> None:
    c.setStrokeColorRGB(0.8, 0.1, 0.1)
    c.setLineWidth(MARK_DRAW_STROKE_PT)
    try:
        c.setLineCap(1)
    except Exception:
        pass
    c.line(x, y, x + size * 0.92, y + size * 0.92)
    c.line(x, y + size * 0.92, x + size * 0.92, y)


def build_overlay_basic(result: GradeResult) -> Overlay:
    marks: List[OverlayMark] = []
    y = 720.0
    for idx, item in enumerate(result.items, start=1):
        label = "✓" if item.score >= item.max_score * 0.5 else "✗"
        marks.append(OverlayMark(tool="bubble", coords=[40.0, y], text=f"{item.score:.0f}/{item.max_score:.0f}"))
        marks.append(OverlayMark(tool="note", coords=[90.0, y], text=f"Q{item.question_id}: {label} {item.rationale}"))
        y -= 28.0
    return Overlay(page=1, marks=marks, meta={"coords_space": "pt"})


def _score_text(total_score: Optional[float], total_max: Optional[float]) -> str:
    if total_max and total_max > 0:
        return f"Score: {float(total_score or 0.0):.0f}/{float(total_max):.0f}"
    return "Score: (unavailable) — NEEDS REVIEW"


def build_minimal_overlay(
    upload_id: str,
    total_score: Optional[float],
    total_max: Optional[float],
) -> Overlay:
    meta = {
        "upload_id": upload_id,
        "generated_at": dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
        "note": "minimal overlay v1",
        "coords_space": "pt",
    }
    return Overlay(
        page=1,
        marks=[OverlayMark(tool="note", coords=[36.0, 36.0], text=_score_text(total_score, total_max))],
        meta=meta,
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


def _convert_point(
    x_px: float,
    y_px: float,
    normalized_size_px: tuple[float, float],
    page_size_pt: tuple[float, float],
) -> tuple[float, float]:
    norm_w, norm_h = normalized_size_px
    page_w, page_h = page_size_pt
    if norm_w <= 0 or norm_h <= 0:
        return x_px, y_px
    sx = page_w / norm_w
    sy = page_h / norm_h
    x_pt = x_px * sx
    y_pt = page_h - (y_px * sy)
    return x_pt, y_pt


def _convert_rect(
    x_px: float,
    y_px: float,
    w_px: float,
    h_px: float,
    normalized_size_px: tuple[float, float],
    page_size_pt: tuple[float, float],
) -> tuple[float, float, float, float]:
    norm_w, norm_h = normalized_size_px
    page_w, page_h = page_size_pt
    if norm_w <= 0 or norm_h <= 0:
        return x_px, y_px, w_px, h_px
    scale_x = page_w / norm_w
    scale_y = page_h / norm_h
    x_pt = x_px * scale_x
    y_bottom_px = y_px + h_px
    y_pt = page_h - (y_bottom_px * scale_y)
    return x_pt, y_pt, w_px * scale_x, h_px * scale_y


def _draw_marks(
    c: canvas.Canvas,
    overlay: Overlay,
    *,
    normalized_size_px: tuple[float, float] | None = None,
    page_size_pt: tuple[float, float] | None = None,
    coords_space: str = "auto",
) -> None:
    use_px_coords = bool(normalized_size_px and page_size_pt)
    if coords_space == "pt":
        use_px_coords = False
    elif coords_space == "px":
        use_px_coords = bool(normalized_size_px and page_size_pt)

    for mark in overlay.marks:
        x_px, y_px = mark.coords[:2]
        if mark.tool == "check":
            x, y = (x_px, y_px)
            if use_px_coords and normalized_size_px and page_size_pt:
                x, y = _convert_point(x_px, y_px, normalized_size_px, page_size_pt)
            _draw_vector_check(c, x, y)
        elif mark.tool == "cross":
            x, y = (x_px, y_px)
            if use_px_coords and normalized_size_px and page_size_pt:
                x, y = _convert_point(x_px, y_px, normalized_size_px, page_size_pt)
            _draw_vector_cross(c, x, y)
        elif mark.tool == "note":
            x, y = (x_px, y_px)
            if use_px_coords and normalized_size_px and page_size_pt:
                x, y = _convert_point(x_px, y_px, normalized_size_px, page_size_pt)
            c.setFont("Helvetica-Bold", NOTE_FONT_SIZE_PT)
            c.drawString(x, y, mark.text or "")
        elif mark.tool == "bubble":
            c.setFont("Helvetica-Bold", BUBBLE_FONT_SIZE_PT)
            if len(mark.coords) >= 4:
                w_px, h_px = mark.coords[2:4]
                x, y, w, h = x_px, y_px, w_px, h_px
                if use_px_coords and normalized_size_px and page_size_pt:
                    norm_w, norm_h = normalized_size_px
                    page_w, page_h = page_size_pt
                    if norm_w > 0 and norm_h > 0:
                        sx = page_w / norm_w
                        sy = page_h / norm_h
                        x = x_px * sx
                        w = w_px * sx
                        h = h_px * sy
                        y = page_h - ((y_px + h_px) * sy)
                c.setFillColorRGB(1, 1, 1)
                c.setLineWidth(1.4)
                c.rect(x, y, w, h, stroke=1, fill=1)
                c.setFillColorRGB(0, 0, 0)
                text_y = y + max(6, (h - BUBBLE_FONT_SIZE_PT) / 2)
                c.drawString(x + 6, text_y, mark.text or "")
            else:
                x, y = (x_px, y_px)
                if use_px_coords and normalized_size_px and page_size_pt:
                    x, y = _convert_point(x_px, y_px, normalized_size_px, page_size_pt)
                c.drawString(x, y, mark.text or "")
        elif mark.tool == "highlight" and len(mark.coords) >= 4:
            w_px, h_px = mark.coords[2:4]
            x, y, w, h = x_px, y_px, w_px, h_px
            if use_px_coords and normalized_size_px and page_size_pt:
                norm_w, norm_h = normalized_size_px
                page_w, page_h = page_size_pt
                if norm_w > 0 and norm_h > 0:
                    sx = page_w / norm_w
                    sy = page_h / norm_h
                    x = x_px * sx
                    w = w_px * sx
                    h = h_px * sy
                    y = page_h - ((y_px + h_px) * sy)
            c.setFillColorRGB(1, 1, 0)
            c.rect(x, y, w, h, stroke=0, fill=1)
            c.setFillColorRGB(0, 0, 0)


def _overlay_pdf_bytes(
    page_width: float,
    page_height: float,
    overlay: Overlay,
    extra_marks: Optional[List[OverlayMark]] = None,
    *,
    normalized_size_px: tuple[float, float] | None = None,
    page_size_pt: tuple[float, float] | None = None,
    coords_space: str = "auto",
    debug: bool = False,
) -> bytes:
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=(page_width, page_height))
    _draw_marks(
        c,
        overlay,
        normalized_size_px=normalized_size_px,
        page_size_pt=page_size_pt,
        coords_space=coords_space,
    )
    if extra_marks:
        _draw_marks(c, Overlay(page=overlay.page, marks=extra_marks), coords_space="pt")
    if debug:
        _draw_debug_stamp(c, page_width, page_height)
    c.save()
    return buf.getvalue()


def _overlay_coords_space(overlay: Optional[Overlay]) -> str:
    if not overlay or not isinstance(overlay.meta, dict):
        return "pt"
    raw = str(overlay.meta.get("coords_space") or "pt").strip().lower()
    return "px" if raw == "px" else "pt"


def _coerce_mark(raw: Any) -> Optional[OverlayMark]:
    if isinstance(raw, OverlayMark):
        return raw
    if isinstance(raw, dict):
        try:
            return OverlayMark(**raw)
        except Exception:
            return None
    return None


def _overlay_marks_by_page(overlay: Optional[Overlay]) -> Dict[int, List[OverlayMark]]:
    if not overlay:
        return {}
    marks_by_page: Dict[int, List[OverlayMark]] = {1: list(overlay.marks or [])}
    meta = overlay.meta if isinstance(overlay.meta, dict) else {}
    raw = meta.get("marks_by_page")
    if not isinstance(raw, dict):
        return marks_by_page

    parsed: Dict[int, List[OverlayMark]] = {}
    for page_key, page_marks in raw.items():
        try:
            page_no = int(page_key)
        except Exception:
            continue
        if page_no <= 0 or not isinstance(page_marks, list):
            continue
        coerced = [_coerce_mark(m) for m in page_marks]
        parsed[page_no] = [m for m in coerced if m is not None]
    if 1 not in parsed and overlay.marks:
        parsed[1] = list(overlay.marks)
    return parsed or marks_by_page


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


def _draw_debug_stamp(c: canvas.Canvas, page_width: float, page_height: float) -> None:
    c.setFont("Helvetica-Bold", 12)
    c.setFillColorRGB(0.1, 0.4, 0.8)
    c.drawString(36.0, max(36.0, page_height - 72.0), "DEBUG: overlay renderer alive")


def render_marked_pdf(
    original_bytes: bytes,
    mime_type: str | None,
    overlay: Optional[Overlay],
    *,
    missing_overlay_text: Optional[str] = None,
    smoke_score_text: Optional[str] = None,
    normalized_size_px: tuple[float, float] | None = None,
) -> bytes:
    debug_stamp = os.getenv("OVERLAY_DEBUG") == "1"
    missing = overlay is None
    banner_text = missing_overlay_text or MISSING_OVERLAY_BANNER
    if missing:
        logger.warning("render_marked_pdf missing overlay; stamping banner=%s", banner_text)

    marks_by_page = _overlay_marks_by_page(overlay)
    mark_count = sum(len(v) for v in marks_by_page.values())
    first_page_marks = marks_by_page.get(1) or []
    first = first_page_marks[0] if first_page_marks else None
    first_info = f"{first.tool}:{(first.text or '')}" if first else "none"
    first_raw = first.coords if first else None
    overlay_coords_space = _overlay_coords_space(overlay)

    if (mime_type or "").lower().endswith("pdf") or original_bytes.startswith(b"%PDF"):
        if PdfReader is None or PdfWriter is None:
            raise RuntimeError("pypdf is required for PDF overlays")
        reader = PdfReader(BytesIO(original_bytes))
        writer = PdfWriter()
        first_page_size: Tuple[float, float] | None = None
        for page_index, page in enumerate(reader.pages):
            page_width = float(page.mediabox.width)
            page_height = float(page.mediabox.height)
            page_no = page_index + 1
            page_marks = marks_by_page.get(page_no) or []
            if page_index == 0:
                first_page_size = (page_width, page_height)
            sx = None
            sy = None
            if normalized_size_px:
                norm_w, norm_h = normalized_size_px
                sx = page_width / norm_w if norm_w else None
                sy = page_height / norm_h if norm_h else None
            logger.info(
                "overlay_scale sx=%s sy=%s page_pt=%s norm_px=%s coords_space=%s page_no=%s marks=%s",
                sx,
                sy,
                (page_width, page_height),
                normalized_size_px,
                overlay_coords_space,
                page_no,
                len(page_marks),
            )
            extra_marks: List[OverlayMark] = []
            if page_index == 0 and not missing and mark_count == 0:
                extra_marks.append(
                    OverlayMark(
                        tool="note",
                        coords=[36.0, max(36.0, page_height - 48.0)],
                        text="OVERLAY EMPTY — NEEDS REVIEW",
                    )
                )
            if page_index == 0 and smoke_score_text:
                extra_marks.append(
                    OverlayMark(
                        tool="bubble",
                        coords=[36.0, max(36.0, page_height - 72.0), 260.0, 26.0],
                        text=smoke_score_text,
                    )
                )

            if missing:
                if page_index == 0:
                    overlay_bytes = _banner_overlay_pdf_bytes(page_width, page_height, banner_text)
                    overlay_reader = PdfReader(BytesIO(overlay_bytes))
                    page.merge_page(overlay_reader.pages[0])
            elif page_marks or extra_marks or debug_stamp:
                page_overlay = Overlay(page=page_no, marks=page_marks, meta=overlay.meta if overlay else None)
                overlay_bytes = _overlay_pdf_bytes(
                    page_width,
                    page_height,
                    page_overlay,
                    extra_marks=extra_marks,
                    normalized_size_px=normalized_size_px,
                    page_size_pt=(page_width, page_height),
                    coords_space=overlay_coords_space,
                    debug=debug_stamp,
                )
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
        first_converted = None
        if first_raw and normalized_size_px and overlay_coords_space == "px" and first_page_size:
            if len(first_raw) >= 4:
                first_converted = _convert_rect(
                    first_raw[0],
                    first_raw[1],
                    first_raw[2],
                    first_raw[3],
                    normalized_size_px,
                    first_page_size,
                )
            else:
                first_converted = _convert_point(
                    first_raw[0],
                    first_raw[1],
                    normalized_size_px,
                    first_page_size,
                )
        logger.info(
            "render_marked_pdf_sizes page_size_pt=%s normalized_size_px=%s first_raw=%s first_converted=%s",
            first_page_size,
            normalized_size_px,
            first_raw,
            first_converted,
        )
        return output.getvalue()

    img = ImageReader(BytesIO(original_bytes))
    width, height = img.getSize()
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=(width, height))
    c.drawImage(img, 0, 0, width=width, height=height)
    sx = None
    sy = None
    if normalized_size_px:
        norm_w, norm_h = normalized_size_px
        sx = width / norm_w if norm_w else None
        sy = height / norm_h if norm_h else None
    logger.info(
        "overlay_scale sx=%s sy=%s page_pt=%s norm_px=%s",
        sx,
        sy,
        (width, height),
        normalized_size_px,
    )
    if missing:
        _draw_missing_overlay_banner(c, width, height, banner_text)
    else:
        _draw_marks(c, overlay, normalized_size_px=normalized_size_px, page_size_pt=(width, height))
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
    if debug_stamp:
        _draw_debug_stamp(c, width, height)
    c.save()
    logger.info(
        "render_marked_pdf: drew background then %s marks first=%s",
        mark_count,
        first_info,
    )
    first_converted = None
    if first_raw and normalized_size_px:
        if len(first_raw) >= 4:
            first_converted = _convert_rect(
                first_raw[0],
                first_raw[1],
                first_raw[2],
                first_raw[3],
                normalized_size_px,
                (width, height),
            )
        else:
            first_converted = _convert_point(
                first_raw[0],
                first_raw[1],
                normalized_size_px,
                (width, height),
            )
    logger.info(
        "render_marked_pdf_sizes page_size_pt=%s normalized_size_px=%s first_raw=%s first_converted=%s",
        (width, height),
        normalized_size_px,
        first_raw,
        first_converted,
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
