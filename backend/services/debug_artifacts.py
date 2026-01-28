from __future__ import annotations

import json
import logging
import os
from io import BytesIO
from typing import Any, Dict, Iterable, List, Tuple

from PIL import Image, ImageDraw

from ..config import SUBMISSIONS_BUCKET
from .coords import pdf_to_px, rect_pdf_to_px
from .storage import upload_bytes
from ..models.schemas import Overlay

logger = logging.getLogger(__name__)


def debug_enabled(flag: bool) -> bool:
    return flag or os.getenv("DEBUG_ARTIFACTS") == "1"


def upload_debug_artifact(
    owner_id: str,
    upload_id: str,
    filename: str,
    data: bytes,
    content_type: str,
) -> str:
    key = f"{owner_id}/debug/{upload_id}/{filename}"
    upload_bytes(SUBMISSIONS_BUCKET, key, data, content_type)
    return f"{SUBMISSIONS_BUCKET}/{key}"


def log_debug(stage: str, fields: Dict[str, Any]) -> None:
    payload = " ".join(f"{k}={v}" for k, v in fields.items())
    logger.info("debug_stage=%s %s", stage, payload)


def _rect_from_bbox(bbox: Iterable[float]) -> Tuple[float, float, float, float] | None:
    vals = list(bbox)
    if len(vals) < 8:
        return None
    xs = vals[0::2]
    ys = vals[1::2]
    return min(xs), min(ys), max(xs), max(ys)


def extract_ocr_rects(ocr_boxes: object) -> Tuple[List[Tuple[float, float, float, float]], List[Tuple[float, float, float, float]]]:
    if not isinstance(ocr_boxes, dict):
        return [], []
    analyze = ocr_boxes.get("analyzeResult") or {}
    read_results = analyze.get("readResults") or []
    line_rects: List[Tuple[float, float, float, float]] = []
    word_rects: List[Tuple[float, float, float, float]] = []
    for page in read_results:
        for line in page.get("lines") or []:
            rect = _rect_from_bbox(line.get("boundingBox") or [])
            if rect:
                line_rects.append(rect)
            for word in line.get("words") or []:
                wrect = _rect_from_bbox(word.get("boundingBox") or [])
                if wrect:
                    word_rects.append(wrect)
    return line_rects, word_rects


def draw_ocr_overlay(image_bytes: bytes, ocr_boxes: object) -> Tuple[bytes, Dict[str, int]]:
    image = Image.open(BytesIO(image_bytes)).convert("RGB")
    draw = ImageDraw.Draw(image)
    line_rects, word_rects = extract_ocr_rects(ocr_boxes)
    for x0, y0, x1, y1 in line_rects:
        draw.rectangle([x0, y0, x1, y1], outline=(0, 200, 0), width=2)
    for x0, y0, x1, y1 in word_rects:
        draw.rectangle([x0, y0, x1, y1], outline=(200, 0, 0), width=1)
    buf = BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue(), {"lines": len(line_rects), "words": len(word_rects)}


def to_png_bytes(image_bytes: bytes) -> bytes:
    image = Image.open(BytesIO(image_bytes)).convert("RGB")
    buf = BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def draw_marks_overlay(
    image_bytes: bytes,
    overlay: Overlay,
    normalized_size: Tuple[float, float],
    page_size: Tuple[float, float],
) -> Tuple[bytes, Dict[str, Any]]:
    image = Image.open(BytesIO(image_bytes)).convert("RGB")
    draw = ImageDraw.Draw(image)
    norm_w, norm_h = normalized_size
    page_w, page_h = page_size
    sx = norm_w / page_w if page_w else 1.0
    sy = norm_h / page_h if page_h else 1.0
    mark_bboxes: List[Tuple[float, float, float, float]] = []

    for mark in overlay.marks:
        if mark.tool in {"check", "cross", "note"}:
            x_pt, y_pt = mark.coords[:2]
            x_px, y_px = pdf_to_px(x_pt, y_pt, normalized_size, page_size)
            size_px = max(8.0, 18.0 * (sx + sy) / 2.0)
            x0 = x_px
            y0 = y_px
            x1 = x_px + size_px
            y1 = y_px + size_px
            if mark.tool == "check":
                draw.line([(x0, y0 + size_px * 0.6), (x0 + size_px * 0.4, y1), (x1, y0)], fill=(0, 140, 0), width=3)
            elif mark.tool == "cross":
                draw.line([(x0, y0), (x1, y1)], fill=(200, 0, 0), width=3)
                draw.line([(x0, y1), (x1, y0)], fill=(200, 0, 0), width=3)
            else:
                draw.ellipse([x0, y0, x1, y1], outline=(60, 60, 60), width=2)
            mark_bboxes.append((x0, y0, x1, y1))
        elif mark.tool in {"bubble", "highlight"} and len(mark.coords) >= 4:
            x_pt, y_pt, w_pt, h_pt = mark.coords[:4]
            x_px, y_px, w_px, h_px = rect_pdf_to_px([x_pt, y_pt, w_pt, h_pt], normalized_size, page_size)
            draw.rectangle([x_px, y_px, x_px + w_px, y_px + h_px], outline=(0, 0, 200), width=2)
            mark_bboxes.append((x_px, y_px, x_px + w_px, y_px + h_px))

    buf = BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue(), {"marks": len(overlay.marks), "mark_bboxes": mark_bboxes}


def serialize_json(data: Any) -> bytes:
    return json.dumps(data, ensure_ascii=True).encode("utf-8")
