from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Tuple

from ..models.schemas import Overlay, OverlayMark, GradeResult
from .coords import px_to_pdf


def build_template_regions_payload(
    regions: Iterable[object],
    size_px: Tuple[int, int],
) -> Dict[str, Any]:
    width, height = size_px
    payload: Dict[str, Any] = {
        "version": 1,
        "page_index": 0,
        "size_px": [float(width), float(height)],
        "regions": {},
    }
    regions_map: Dict[str, Any] = {}
    for region in regions:
        qid = str(getattr(region, "qid", "") or "").strip()
        if not qid:
            continue
        answer_box = getattr(region, "answer_box", None)
        if not answer_box:
            continue
        x, y, w, h = answer_box
        box = {
            "x": float(x) / float(width) if width else 0.0,
            "y": float(y) / float(height) if height else 0.0,
            "w": float(w) / float(width) if width else 0.0,
            "h": float(h) / float(height) if height else 0.0,
        }
        entry: Dict[str, Any] = {"answer_box": box}
        expected = getattr(region, "expected_answer_text", None)
        if expected is not None:
            entry["expected_answer_text"] = expected
        regions_map[qid] = entry
    payload["regions"] = regions_map
    return payload


def parse_regions_payload(payload: object) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Any]]:
    if isinstance(payload, dict) and isinstance(payload.get("regions"), dict):
        meta = {
            "version": payload.get("version"),
            "page_index": int(payload.get("page_index") or 0),
            "size_px": payload.get("size_px"),
        }
        return payload.get("regions") or {}, meta
    if isinstance(payload, list):
        regions_map: Dict[str, Dict[str, Any]] = {}
        for idx, region in enumerate(payload, start=1):
            if not isinstance(region, dict):
                continue
            qid = str(region.get("qid") or f"Q{idx}")
            entry: Dict[str, Any] = {}
            if region.get("answer_box"):
                entry["answer_box"] = region.get("answer_box")
            if region.get("expected_answer_text") is not None:
                entry["expected_answer_text"] = region.get("expected_answer_text")
            regions_map[qid] = entry
        return regions_map, {"version": 0, "page_index": 0, "size_px": None}
    return {}, {"version": None, "page_index": 0, "size_px": None}


def expected_answers_from_regions(regions_map: Dict[str, Dict[str, Any]]) -> Dict[str, str]:
    answers: Dict[str, str] = {}
    for qid, entry in regions_map.items():
        answers[qid] = str(entry.get("expected_answer_text") or "").strip()
    return answers


def extract_answers_from_regions(
    ocr_boxes: object,
    regions_payload: object,
    *,
    fallback_size: Optional[Tuple[float, float]] = None,
) -> Tuple[Dict[str, str], List[str]]:
    regions_map, meta = parse_regions_payload(regions_payload)
    if not regions_map:
        return {}, []
    words = _extract_word_boxes(ocr_boxes)
    page_sizes = _extract_page_sizes(ocr_boxes)
    answers: Dict[str, str] = {}
    missing: List[str] = []
    for qid, entry in regions_map.items():
        answer_box = entry.get("answer_box") or {}
        if not isinstance(answer_box, dict):
            missing.append(qid)
            answers[qid] = ""
            continue
        page_index = int(entry.get("page_index") or meta.get("page_index") or 0)
        size = page_sizes.get(page_index) or _size_from_meta(meta) or fallback_size
        if not size:
            missing.append(qid)
            answers[qid] = ""
            continue
        rect = _answer_box_to_px(answer_box, size)
        if not rect:
            missing.append(qid)
            answers[qid] = ""
            continue
        x0, y0, x1, y1 = rect
        page_words = [w for w in words if w["page_index"] == page_index]
        hits = []
        for w in page_words:
            cx = (w["rect"][0] + w["rect"][2]) / 2.0
            cy = (w["rect"][1] + w["rect"][3]) / 2.0
            if x0 <= cx <= x1 and y0 <= cy <= y1:
                hits.append(w)
        hits.sort(key=lambda w: (w["rect"][1], w["rect"][0]))
        if not hits:
            missing.append(qid)
            answers[qid] = ""
            continue
        answers[qid] = " ".join(w["text"] for w in hits if w["text"])
    return answers, missing


def build_overlay_from_regions(
    grade_result: GradeResult,
    regions_payload: object,
    normalized_size_px: Tuple[float, float],
    page_size_pt: Tuple[float, float],
) -> Tuple[Overlay, int, int, int, List[str]]:
    regions_map, meta = parse_regions_payload(regions_payload)
    norm_w, norm_h = normalized_size_px
    if norm_w <= 0 or norm_h <= 0:
        size = _size_from_meta(meta)
        if size:
            norm_w, norm_h = size
        else:
            norm_w, norm_h = page_size_pt
    marks: List[OverlayMark] = []
    unplaced: List[str] = []
    placed = 0
    skipped_missing = 0
    skipped_needs_review = 0

    score_w = 140.0
    score_h = 26.0
    score_margin = 24.0
    score_x = page_size_pt[0] - score_w - score_margin
    score_y = page_size_pt[1] - score_h - score_margin
    marks.append(
        OverlayMark(
            tool="bubble",
            coords=[score_x, score_y, score_w, score_h],
            text=f"Score: {grade_result.total_score:.0f}/{grade_result.total_max:.0f}",
        )
    )

    for item in grade_result.items:
        if item.low_confidence:
            skipped_needs_review += 1
            continue
        region = regions_map.get(item.question_id)
        if not region:
            unplaced.append(item.question_id)
            skipped_missing += 1
            continue
        answer_box = region.get("answer_box") or {}
        rect = _answer_box_to_px(answer_box, (norm_w, norm_h))
        if not rect:
            unplaced.append(item.question_id)
            skipped_missing += 1
            continue
        x0, y0, x1, y1 = rect
        w = x1 - x0
        h = y1 - y0
        anchor_x_px = x0 + w - 18.0
        anchor_y_px = y0 + 6.0
        x_pt, y_pt = px_to_pdf(anchor_x_px, anchor_y_px, (norm_w, norm_h), page_size_pt)
        tool = "check" if item.score >= item.max_score else "cross"
        marks.append(OverlayMark(tool=tool, coords=[x_pt, y_pt], text=None))
        placed += 1

    return Overlay(page=1, marks=marks), placed, skipped_missing, skipped_needs_review, unplaced


def _size_from_meta(meta: Dict[str, Any]) -> Optional[Tuple[float, float]]:
    size = meta.get("size_px")
    if isinstance(size, (list, tuple)) and len(size) >= 2:
        try:
            return float(size[0]), float(size[1])
        except Exception:
            return None
    return None


def _answer_box_to_px(answer_box: Dict[str, Any], size: Tuple[float, float]) -> Optional[Tuple[float, float, float, float]]:
    try:
        x = float(answer_box.get("x") or 0.0)
        y = float(answer_box.get("y") or 0.0)
        w = float(answer_box.get("w") or 0.0)
        h = float(answer_box.get("h") or 0.0)
    except Exception:
        return None
    if w <= 0 or h <= 0:
        return None
    max_val = max(x, y, w, h)
    if max_val <= 1.5:
        sx, sy = size
        return x * sx, y * sy, (x + w) * sx, (y + h) * sy
    return x, y, x + w, y + h


def _rect_from_bbox(bbox: Iterable[float]) -> Optional[Tuple[float, float, float, float]]:
    vals = list(bbox)
    if len(vals) < 8:
        return None
    xs = vals[0::2]
    ys = vals[1::2]
    return min(xs), min(ys), max(xs), max(ys)


def _extract_page_sizes(ocr_boxes: object) -> Dict[int, Tuple[float, float]]:
    if not isinstance(ocr_boxes, dict):
        return {}
    analyze = ocr_boxes.get("analyzeResult") or {}
    read_results = analyze.get("readResults") or []
    sizes: Dict[int, Tuple[float, float]] = {}
    for page_index, page in enumerate(read_results):
        w = float(page.get("width") or 0.0)
        h = float(page.get("height") or 0.0)
        if w > 0 and h > 0:
            sizes[page_index] = (w, h)
    return sizes


def _extract_word_boxes(ocr_boxes: object) -> List[Dict[str, Any]]:
    if not isinstance(ocr_boxes, dict):
        return []
    analyze = ocr_boxes.get("analyzeResult") or {}
    read_results = analyze.get("readResults") or []
    words: List[Dict[str, Any]] = []
    for page_index, page in enumerate(read_results):
        for line in page.get("lines") or []:
            for word in line.get("words") or []:
                text = str(word.get("text") or "").strip()
                bbox = word.get("boundingBox") or []
                rect = _rect_from_bbox(bbox)
                if not rect:
                    continue
                words.append(
                    {
                        "text": text,
                        "rect": rect,
                        "page_index": page_index,
                    }
                )
    return words
