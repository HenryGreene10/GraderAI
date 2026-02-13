from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

from ..models.schemas import Overlay, OverlayMark, GradeResult
from .coords import px_to_pdf
from .scoring import parse_quotient_remainder
from .template import normalize_answer_text


_ANSWER_REM_RE = re.compile(r"^\d+\s*[Rr]\s*\d+$")
_ANSWER_NUMERIC_RE = re.compile(r"^\d+(?:\.\d+)?(?:/\d+)?$")
_ANSWER_ANY_DIGIT_RE = re.compile(r"\d")
_DIVISION_EXPRESSION_RE = re.compile(r"^\d+\)\d+$")
_Q_LABEL_RE = re.compile(r"^[\(\[]?\s*Q\s*\d{0,2}\s*[\)\].,:;]?$", re.I)
_MIN_ANSWER_SCORE = 45


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
        raw = str(entry.get("expected_answer_text") or "").strip()
        parsed = parse_quotient_remainder(raw)
        if parsed is not None:
            answers[qid] = f"{parsed[0]} R{parsed[1]}"
        else:
            answers[qid] = raw
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
        page_words = [w for w in words if w["page_index"] == page_index]
        expected_hint = str(entry.get("expected_answer_text") or "")
        text, score = _best_text_for_rect(
            page_words,
            rect,
            expected_hint=expected_hint,
            focus_rect=rect,
        )
        region_rect = _entry_region_box_to_px(entry, size)
        if region_rect:
            fallback_text, fallback_score = _best_text_for_rect(
                page_words,
                region_rect,
                expected_hint=expected_hint,
                focus_rect=rect,
            )
            if fallback_score > score:
                text, score = fallback_text, fallback_score
        if score < _MIN_ANSWER_SCORE or not text:
            missing.append(qid)
            answers[qid] = ""
            continue
        answers[qid] = text
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


def _entry_region_box_to_px(
    entry: Dict[str, Any],
    size: Tuple[float, float],
) -> Optional[Tuple[float, float, float, float]]:
    region_box = entry.get("region_box_px")
    if isinstance(region_box, (list, tuple)) and len(region_box) >= 4:
        x, y, w, h = region_box[0], region_box[1], region_box[2], region_box[3]
    else:
        return None
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
        for line_index, line in enumerate(page.get("lines") or []):
            for word_index, word in enumerate(line.get("words") or []):
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
                        "line_index": line_index,
                        "word_index": word_index,
                    }
                )
    return words


def _rect_center_in(rect: Tuple[float, float, float, float], region: Tuple[float, float, float, float]) -> bool:
    x0, y0, x1, y1 = rect
    rx0, ry0, rx1, ry1 = region
    cx = (x0 + x1) / 2.0
    cy = (y0 + y1) / 2.0
    return rx0 <= cx <= rx1 and ry0 <= cy <= ry1


def _candidate_score(text: str) -> int:
    raw_compact = re.sub(r"\s+", " ", str(text or "").strip())
    normalized = normalize_answer_text(text)
    compact = re.sub(r"\s+", " ", str(normalized or "").strip())
    if not compact:
        return 0
    if _Q_LABEL_RE.fullmatch(compact):
        return 0
    tokens = compact.split(" ")
    base = 0
    if _ANSWER_REM_RE.fullmatch(compact):
        base = 140
    elif _ANSWER_NUMERIC_RE.fullmatch(compact):
        base = 100
    elif _ANSWER_ANY_DIGIT_RE.search(compact):
        base = 60
    raw_tokens = [tok.strip() for tok in raw_compact.split(" ") if tok.strip()]
    if any(_DIVISION_EXPRESSION_RE.fullmatch(tok) for tok in raw_tokens):
        base -= 40
    if len(tokens) > 3:
        base -= 10
    return max(0, base)


def _center_distance(
    rect_a: Tuple[float, float, float, float],
    rect_b: Tuple[float, float, float, float],
) -> float:
    ax0, ay0, ax1, ay1 = rect_a
    bx0, by0, bx1, by1 = rect_b
    acx = (ax0 + ax1) / 2.0
    acy = (ay0 + ay1) / 2.0
    bcx = (bx0 + bx1) / 2.0
    bcy = (by0 + by1) / 2.0
    return float(((acx - bcx) ** 2 + (acy - bcy) ** 2) ** 0.5)


def _format_match_bonus(expected_hint: str, candidate_text: str) -> int:
    expected = parse_quotient_remainder(str(expected_hint or ""))
    if expected is None:
        return 0
    candidate = parse_quotient_remainder(str(candidate_text or ""))
    if candidate is None:
        return -8
    if candidate == expected:
        return 24
    expected_has_rem = int(expected[1]) != 0
    candidate_has_rem = int(candidate[1]) != 0
    if expected_has_rem and candidate_has_rem:
        return 16
    if expected_has_rem and not candidate_has_rem:
        return -18
    if (not expected_has_rem) and (not candidate_has_rem):
        return 8
    return -4


def _best_text_for_rect(
    page_words: List[Dict[str, Any]],
    rect: Tuple[float, float, float, float],
    *,
    expected_hint: str = "",
    focus_rect: Optional[Tuple[float, float, float, float]] = None,
) -> Tuple[str, int]:
    hits = [w for w in page_words if _rect_center_in(w["rect"], rect)]
    if not hits:
        return "", 0
    hits.sort(key=lambda w: (int(w.get("line_index") or 0), w["rect"][0], int(w.get("word_index") or 0)))
    candidates: List[Tuple[int, int, float, str]] = []

    def _total_score(text: str, cand_rect: Tuple[float, float, float, float]) -> Tuple[int, float]:
        base = _candidate_score(text)
        if base <= 0:
            return 0, 9999.0
        format_bonus = _format_match_bonus(expected_hint, text)
        distance = _center_distance(cand_rect, focus_rect or rect)
        focus_w = max(1.0, abs((focus_rect or rect)[2] - (focus_rect or rect)[0]))
        focus_h = max(1.0, abs((focus_rect or rect)[3] - (focus_rect or rect)[1]))
        focus_scale = max(18.0, min(focus_w, focus_h) * 0.9)
        distance_penalty = min(26.0, distance / focus_scale)
        total = int(round(base + format_bonus - distance_penalty))
        return total, distance

    for w in hits:
        text = str(w.get("text") or "").strip()
        if not text:
            continue
        score, distance = _total_score(text, w["rect"])
        candidates.append((score, 1, distance, normalize_answer_text(text)))

    by_line: Dict[int, List[Dict[str, Any]]] = {}
    for w in hits:
        by_line.setdefault(int(w.get("line_index") or 0), []).append(w)
    for line_words in by_line.values():
        ordered = sorted(line_words, key=lambda w: (w["rect"][0], int(w.get("word_index") or 0)))
        n = len(ordered)
        for i in range(n):
            for span_len in (2, 3):
                j = i + span_len
                if j > n:
                    continue
                chunk = ordered[i:j]
                joined = " ".join(str(w.get("text") or "").strip() for w in chunk).strip()
                if not joined:
                    continue
                left = min(float(item["rect"][0]) for item in chunk)
                top = min(float(item["rect"][1]) for item in chunk)
                right = max(float(item["rect"][2]) for item in chunk)
                bottom = max(float(item["rect"][3]) for item in chunk)
                chunk_rect = (left, top, right, bottom)
                score, distance = _total_score(joined, chunk_rect)
                candidates.append((score, span_len, distance, normalize_answer_text(joined)))
        # Recover noisy OCR where numeric quotient and remainder token are separated.
        for i in range(n):
            left_token = ordered[i]
            left_text = str(left_token.get("text") or "").strip()
            if not _ANSWER_NUMERIC_RE.fullmatch(left_text):
                continue
            for j in range(i + 1, n):
                right_token = ordered[j]
                right_text = str(right_token.get("text") or "").strip()
                if not re.fullmatch(r"[Rr]\s*[0-9]+", right_text):
                    continue
                joined = f"{left_text} {right_text}".strip()
                left = min(float(left_token["rect"][0]), float(right_token["rect"][0]))
                top = min(float(left_token["rect"][1]), float(right_token["rect"][1]))
                right = max(float(left_token["rect"][2]), float(right_token["rect"][2]))
                bottom = max(float(left_token["rect"][3]), float(right_token["rect"][3]))
                pair_rect = (left, top, right, bottom)
                score, distance = _total_score(joined, pair_rect)
                candidates.append((score, 2, distance, normalize_answer_text(joined)))

    joined_all = " ".join(str(w.get("text") or "").strip() for w in hits).strip()
    if joined_all:
        left = min(float(item["rect"][0]) for item in hits)
        top = min(float(item["rect"][1]) for item in hits)
        right = max(float(item["rect"][2]) for item in hits)
        bottom = max(float(item["rect"][3]) for item in hits)
        all_rect = (left, top, right, bottom)
        score, distance = _total_score(joined_all, all_rect)
        candidates.append((score, max(1, len(joined_all.split())), distance, normalize_answer_text(joined_all)))

    if not candidates:
        return "", 0
    candidates.sort(key=lambda item: (item[0], -item[1], -item[2], len(item[3])), reverse=True)
    best_score, _span_len, _distance, best_text = candidates[0]
    return best_text, int(best_score)
