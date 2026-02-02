from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Iterable, List, Optional, Tuple

from ..models.schemas import Overlay, OverlayMark
from .coords import px_to_pdf
from .llm_grader import LLMAnswer, CONFIDENCE_REVIEW_THRESHOLD

logger = logging.getLogger(__name__)


@dataclass
class HandwritingLine:
    text: str
    bbox: List[float]
    page_index: int
    page_width: float
    page_height: float


@dataclass
class DebugAnchor:
    question_id: str
    anchor_px: Tuple[float, float]
    bbox_px: Tuple[float, float, float, float]
    source: str


@dataclass
class DebugLayout:
    boxes_px: List[Tuple[float, float, float, float]]
    anchors: List[DebugAnchor]


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


def _question_pattern(qid: str) -> re.Pattern[str]:
    safe = re.escape(str(qid))
    return re.compile(rf"^(?:q\s*)?{safe}\b")


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


def _find_question_line(qid: str, lines: List[HandwritingLine], used: set[int]) -> Optional[HandwritingLine]:
    pattern = _question_pattern(qid)
    for idx, line in enumerate(lines):
        if idx in used:
            continue
        text = _normalize_text(line.text)
        if pattern.search(text):
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
    tool = "check" if answer.correct else "cross"
    return tool, None, low_conf


def _infer_normalized_size(lines: List[HandwritingLine]) -> Tuple[float, float]:
    max_w = 0.0
    max_h = 0.0
    for line in lines:
        if line.page_width:
            max_w = max(max_w, line.page_width)
        if line.page_height:
            max_h = max(max_h, line.page_height)
    return max_w, max_h


def build_overlay_from_answers(
    answers: Iterable[LLMAnswer],
    ocr_boxes: object,
    page_sizes: List[Tuple[float, float]],
    *,
    normalized_size: Tuple[float, float] = (0.0, 0.0),
    total_score: float = 0.0,
    total_max: float = 0.0,
) -> Tuple[Overlay, bool, List[str], DebugLayout]:
    handwriting_lines = _extract_lines(ocr_boxes, handwriting_only=True)
    if not handwriting_lines:
        logger.warning("No handwriting lines found in OCR boxes; using all lines if present.")
        needs_review = True
    else:
        needs_review = False

    lines_all = _extract_lines(ocr_boxes, handwriting_only=False)
    lines = handwriting_lines if handwriting_lines else lines_all
    norm_w, norm_h = normalized_size
    if norm_w <= 0 or norm_h <= 0:
        norm_w, norm_h = _infer_normalized_size(lines_all)
    if norm_w <= 0 or norm_h <= 0:
        norm_w, norm_h = page_sizes[0] if page_sizes else (1.0, 1.0)

    used: set[int] = set()
    marks: List[OverlayMark] = []
    unplaced: List[str] = []
    debug_anchors: List[DebugAnchor] = []
    debug_boxes: List[Tuple[float, float, float, float]] = []
    page_size = page_sizes[0] if page_sizes else (612.0, 792.0)

    score_box_w = 140.0
    score_box_h = 26.0
    score_margin = 24.0
    score_x = page_size[0] - score_box_w - score_margin
    score_y = page_size[1] - score_box_h - score_margin
    if total_max > 0:
        marks.append(
            OverlayMark(
                tool="bubble",
                coords=[score_x, score_y, score_box_w, score_box_h],
                text=f"Score: {total_score:.0f}/{total_max:.0f}",
            )
        )

    for idx, answer in enumerate(answers, start=1):
        tool, text, low_conf = _mark_for_answer(answer)
        if low_conf:
            needs_review = True

        line = _find_question_line(answer.question_id, lines_all, used)
        source = "question_number" if line else ""
        if not line:
            line = _match_line(answer.student_answer, lines, used)
            source = "answer_match" if line else ""
        if not line:
            line = _fallback_line(lines, used)
            source = "fallback" if line else ""

        if line:
            rect = _bbox_to_rect(line.bbox)
            if rect:
                x0, y0, x1, y1 = rect
                pad_x_px = 12.0
                pad_y_px = 4.0
                anchor_x_px = x1 + pad_x_px
                anchor_y_px = y0 + pad_y_px
                mark_x, mark_y = px_to_pdf(anchor_x_px, anchor_y_px, (norm_w, norm_h), page_size)
                marks.append(OverlayMark(tool=tool, coords=[mark_x, mark_y], text=text))
                debug_anchors.append(
                    DebugAnchor(
                        question_id=str(answer.question_id),
                        anchor_px=(anchor_x_px, anchor_y_px),
                        bbox_px=(x0, y0, x1, y1),
                        source=source or "unknown",
                    )
                )
                continue

        unplaced.append(f"Q{answer.question_id}")
        needs_review = True

        margin_y = page_size[1] - (48 + idx * 20)
        label = text or ("✓" if tool == "check" else "✗")
        marks.append(OverlayMark(tool="note", coords=[36.0, margin_y], text=f"Q{answer.question_id}: {label}"))

    for line in lines_all:
        rect = _bbox_to_rect(line.bbox)
        if rect:
            debug_boxes.append(rect)

    if unplaced:
        warning = "Needs review: " + ", ".join(unplaced) + " (unplaced marks)"
        warn_w = min(360.0, page_size[0] - 48.0)
        warn_h = 28.0
        warn_x = 24.0
        warn_y = score_y - warn_h - 8.0 if score_y - warn_h - 8.0 > 0 else (page_size[1] - warn_h - 24.0)
        marks.append(OverlayMark(tool="bubble", coords=[warn_x, warn_y, warn_w, warn_h], text=warning))

    overlay = Overlay(page=1, marks=marks)
    return overlay, needs_review, unplaced, DebugLayout(boxes_px=debug_boxes, anchors=debug_anchors)
