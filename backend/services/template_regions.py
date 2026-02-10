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
        "template_width_px": float(width),
        "template_height_px": float(height),
        "regions": [],
    }
    entries: List[Tuple[int, float, float, object]] = []
    for idx, region in enumerate(regions, start=1):
        answer_box = getattr(region, "answer_box", None)
        if not answer_box:
            continue
        x, y, _w, _h = answer_box
        region_index = getattr(region, "index", idx)
        try:
            region_index = int(region_index)
        except Exception:
            region_index = idx
        entries.append((region_index, float(y), float(x), region))
    entries.sort(key=lambda item: (item[0], item[1], item[2]))
    for idx, (_region_index, _y, _x, region) in enumerate(entries, start=1):
        answer_box = getattr(region, "answer_box", None)
        if not answer_box:
            continue
        x, y, w, h = answer_box
        qid = str(getattr(region, "qid", "") or f"Q{idx}")
        page_index = int(getattr(region, "page_index", 0) or 0)
        entry: Dict[str, Any] = {
            "qid": qid,
            "page_index": page_index,
            "bbox_px": [float(x), float(y), float(w), float(h)],
            "source": str(getattr(region, "label_method", "") or "answer_box"),
        }
        region_box = getattr(region, "region", None)
        if isinstance(region_box, (list, tuple)) and len(region_box) >= 4:
            entry["region_box_px"] = [
                float(region_box[0]),
                float(region_box[1]),
                float(region_box[2]),
                float(region_box[3]),
            ]
        expected = getattr(region, "expected_answer_text", None)
        if expected is not None:
            entry["expected_answer_text"] = expected
        payload["regions"].append(entry)
    return payload


def parse_regions_payload(payload: object) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Any]]:
    if isinstance(payload, dict) and isinstance(payload.get("regions"), list):
        meta = {
            "version": payload.get("version"),
            "page_index": int(payload.get("page_index") or 0),
            "template_width_px": payload.get("template_width_px"),
            "template_height_px": payload.get("template_height_px"),
        }
        regions_map: Dict[str, Dict[str, Any]] = {}
        for idx, region in enumerate(payload.get("regions") or [], start=1):
            if not isinstance(region, dict):
                continue
            qid = str(region.get("qid") or f"Q{idx}")
            regions_map[qid] = region
        return regions_map, meta
    if isinstance(payload, dict) and isinstance(payload.get("regions"), dict):
        meta = {
            "version": payload.get("version"),
            "page_index": int(payload.get("page_index") or 0),
            "template_width_px": payload.get("template_width_px"),
            "template_height_px": payload.get("template_height_px"),
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
        return regions_map, {"version": 0, "page_index": 0, "template_width_px": None, "template_height_px": None}
    return {}, {"version": None, "page_index": 0, "template_width_px": None, "template_height_px": None}


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
        page_index = int(entry.get("page_index") or meta.get("page_index") or 0)
        size = page_sizes.get(page_index) or _size_from_meta(meta) or fallback_size
        if not size:
            missing.append(qid)
            answers[qid] = ""
            continue
        rect = _entry_bbox_to_px(entry, size, meta)
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
    *,
    page_sizes_pt: Optional[List[Tuple[float, float]]] = None,
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
    marks_by_page: Dict[int, List[OverlayMark]] = {}
    fallback_count_by_page: Dict[int, int] = {}

    def _page_size_for_index(page_index: int) -> Tuple[float, float]:
        if page_sizes_pt and 0 <= page_index < len(page_sizes_pt):
            w, h = page_sizes_pt[page_index]
            if w > 0 and h > 0:
                return (w, h)
        return page_size_pt

    def _fallback_mark(item_id: str, item_symbol: str, page_no: int) -> None:
        idx = fallback_count_by_page.get(page_no, 0)
        page_w, page_h = _page_size_for_index(page_no - 1)
        y = max(24.0, page_h - 56.0 - idx * 18.0)
        mark = OverlayMark(tool="note", coords=[24.0, y], text=f"{item_id}: {item_symbol} (fallback)")
        fallback_count_by_page[page_no] = idx + 1
        if page_no == 1:
            marks.append(mark)
        marks_by_page.setdefault(page_no, []).append(mark)

    score_w = 140.0
    score_h = 26.0
    score_margin = 24.0
    score_x = page_size_pt[0] - score_w - score_margin
    score_y = page_size_pt[1] - score_h - score_margin
    score_mark = OverlayMark(
        tool="bubble",
        coords=[score_x, score_y, score_w, score_h],
        text=f"Score: {grade_result.total_score:.0f}/{grade_result.total_max:.0f}",
    )
    marks.append(score_mark)
    marks_by_page.setdefault(1, []).append(score_mark)

    for item in grade_result.items:
        region = regions_map.get(item.question_id)
        symbol = "REVIEW" if item.low_confidence else ("✓" if item.score >= item.max_score else "✗")
        if not region:
            unplaced.append(item.question_id)
            skipped_missing += 1
            _fallback_mark(item.question_id, symbol, 1)
            continue
        page_index = int(region.get("page_index") or meta.get("page_index") or 0)
        page_no = page_index + 1
        page_size_for_mark = _page_size_for_index(page_index)
        rect = _entry_bbox_to_px(region, (norm_w, norm_h), meta)
        if not rect:
            unplaced.append(item.question_id)
            skipped_missing += 1
            _fallback_mark(item.question_id, symbol, page_no)
            continue
        x0, y0, x1, y1 = rect
        w = x1 - x0
        h = y1 - y0
        anchor_x_px = x0 + w - 18.0
        anchor_y_px = y0 + 6.0
        x_pt, y_pt = px_to_pdf(anchor_x_px, anchor_y_px, (norm_w, norm_h), page_size_for_mark)
        if item.low_confidence:
            skipped_needs_review += 1
            mark = OverlayMark(tool="note", coords=[x_pt, y_pt], text="REVIEW")
        else:
            tool = "check" if item.score >= item.max_score else "cross"
            mark = OverlayMark(tool=tool, coords=[x_pt, y_pt], text=None)
        if page_no == 1:
            marks.append(mark)
        marks_by_page.setdefault(page_no, []).append(mark)
        placed += 1

    meta_out: Dict[str, Any] = {"coords_space": "pt"}
    if any(page_no != 1 for page_no in marks_by_page.keys()):
        meta_out["marks_by_page"] = {
            str(page_no): [m.model_dump() for m in page_marks]
            for page_no, page_marks in marks_by_page.items()
        }

    return Overlay(page=1, marks=marks, meta=meta_out), placed, skipped_missing, skipped_needs_review, unplaced


def _size_from_meta(meta: Dict[str, Any]) -> Optional[Tuple[float, float]]:
    width = meta.get("template_width_px")
    height = meta.get("template_height_px")
    if width and height:
        try:
            return float(width), float(height)
        except Exception:
            return None
    size = meta.get("size_px")
    if isinstance(size, (list, tuple)) and len(size) >= 2:
        try:
            return float(size[0]), float(size[1])
        except Exception:
            return None
    return None


def _entry_bbox_to_px(
    entry: Dict[str, Any],
    size: Tuple[float, float],
    meta: Dict[str, Any],
) -> Optional[Tuple[float, float, float, float]]:
    bbox = entry.get("bbox_px")
    if isinstance(bbox, (list, tuple)) and len(bbox) >= 4:
        x, y, w, h = bbox[0], bbox[1], bbox[2], bbox[3]
    else:
        answer_box = entry.get("answer_box") or {}
        if not isinstance(answer_box, dict):
            return None
        x = answer_box.get("x")
        y = answer_box.get("y")
        w = answer_box.get("w")
        h = answer_box.get("h")
    try:
        x = float(x or 0.0)
        y = float(y or 0.0)
        w = float(w or 0.0)
        h = float(h or 0.0)
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
