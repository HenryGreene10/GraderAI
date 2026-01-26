from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Iterable, List, Optional, Tuple

from ..models.schemas import Overlay, OverlayMark
from .llm_grader import LLMAnswer, CONFIDENCE_REVIEW_THRESHOLD

logger = logging.getLogger(__name__)


@dataclass
class HandwritingLine:
    text: str
    bbox: List[float]
    page_index: int
    page_width: float
    page_height: float


def _is_handwriting(line: dict) -> bool:
    appearance = line.get("appearance") or {}
    style = appearance.get("style")
    if isinstance(style, dict):
        return str(style.get("name") or "").lower() == "handwriting"
    if isinstance(style, list):
        return any(
            str(item.get("name") or "").lower() == "handwriting"
            for item in style
            if isinstance(item, dict)
        )
    return False


def _extract_lines(ocr_boxes: object, handwriting_only: bool) -> List[HandwritingLine]:
    if not isinstance(ocr_boxes, dict):
        return []
    analyze = ocr_boxes.get("analyzeResult") or {}
    read_results = analyze.get("readResults") or []
    lines: List[HandwritingLine] = []
    for page_index, page in enumerate(read_results):
        page_width = float(page.get("width") or 0) or 0.0
        page_height = float(page.get("height") or 0) or 0.0
        for line in page.get("lines") or []:
            if not isinstance(line, dict):
                continue
            if handwriting_only and not _is_handwriting(line):
                continue
            text = str(line.get("text") or "").strip()
            bbox = line.get("boundingBox") or []
            if not isinstance(bbox, list) or len(bbox) < 8:
                continue
            lines.append(
                HandwritingLine(
                    text=text,
                    bbox=[float(v) for v in bbox],
                    page_index=page_index,
                    page_width=page_width,
                    page_height=page_height,
                )
            )
    return lines


def _bbox_to_rect(bbox: List[float]) -> Optional[Tuple[float, float, float, float]]:
    if not bbox or len(bbox) < 8:
        return None
    xs = bbox[0::2]
    ys = bbox[1::2]
    return min(xs), min(ys), max(xs), max(ys)


def _normalize_text(value: str) -> str:
    return " ".join((value or "").lower().split())


def _match_line(answer_text: str, lines: List[HandwritingLine], used: set[int]) -> Optional[HandwritingLine]:
    needle = _normalize_text(answer_text)
    if not needle:
        return None
    for idx, line in enumerate(lines):
        if idx in used:
            continue
        haystack = _normalize_text(line.text)
        if needle in haystack:
            used.add(idx)
            return line
    return None


def _fallback_line(lines: List[HandwritingLine], used: set[int]) -> Optional[HandwritingLine]:
    for idx, line in enumerate(lines):
        if idx not in used:
            used.add(idx)
            return line
    return None


def _mark_for_answer(answer: LLMAnswer) -> Tuple[str, Optional[str], bool]:
    low_conf = answer.confidence < CONFIDENCE_REVIEW_THRESHOLD
    if low_conf:
        return "note", "Review", True
    if answer.correct:
        return "check", None, False
    return "cross", None, False


def build_overlay_from_answers(
    answers: Iterable[LLMAnswer],
    ocr_boxes: object,
    page_sizes: List[Tuple[float, float]],
) -> Tuple[Overlay, bool]:
    handwriting_lines = _extract_lines(ocr_boxes, handwriting_only=True)
    if not handwriting_lines:
        logger.warning("No handwriting lines found in OCR boxes; using all lines if present.")
        needs_review = True
    else:
        needs_review = False
    lines = handwriting_lines if handwriting_lines else _extract_lines(ocr_boxes, handwriting_only=False)

    used: set[int] = set()
    marks: List[OverlayMark] = []

    for idx, answer in enumerate(answers, start=1):
        tool, text, low_conf = _mark_for_answer(answer)
        if low_conf:
            needs_review = True

        line = _match_line(answer.student_answer, lines, used) or _fallback_line(lines, used)
        if line:
            rect = _bbox_to_rect(line.bbox)
            if rect:
                x0, y0, x1, y1 = rect
                page_width = line.page_width or page_sizes[min(line.page_index, len(page_sizes) - 1)][0]
                page_height = line.page_height or page_sizes[min(line.page_index, len(page_sizes) - 1)][1]
                pdf_width, pdf_height = page_sizes[min(line.page_index, len(page_sizes) - 1)]
                sx = pdf_width / page_width if page_width else 1.0
                sy = pdf_height / page_height if page_height else 1.0

                mark_x = (x1 + 8.0) * sx
                mark_y = (pdf_height - y1) * sy
                marks.append(OverlayMark(tool=tool, coords=[mark_x, mark_y], text=text))
                continue

        # Margin fallback
        page_width, page_height = page_sizes[0]
        margin_y = page_height - (36 + idx * 24)
        label = text or ("✓" if tool == "check" else "✗")
        marks.append(OverlayMark(tool="note", coords=[36.0, margin_y], text=f"Q{answer.question_id}: {label}"))

    overlay = Overlay(page=1, marks=marks)
    return overlay, needs_review
