from __future__ import annotations

from dataclasses import dataclass
import math
import re
from typing import Any, Dict, List, Optional, Tuple

from .template import normalize_answer_text


@dataclass
class AnchorRegion:
    qid: str
    region: Tuple[float, float, float, float]
    answer_box: Tuple[float, float, float, float]
    expected_answer_text: str
    label_method: str
    index: int
    page_index: int = 0


@dataclass
class _Token:
    text: str
    page_index: int
    line_index: int
    word_index: int
    x: float
    y: float
    w: float
    h: float

    @property
    def center(self) -> Tuple[float, float]:
        return (self.x + self.w / 2.0, self.y + self.h / 2.0)

    @property
    def rect(self) -> Tuple[float, float, float, float]:
        return (self.x, self.y, self.w, self.h)


@dataclass
class _Anchor:
    page_index: int
    line_index: int
    x: float
    y: float
    w: float
    h: float
    parsed_num: Optional[int]
    confidence: int
    label_method: str
    text: str

    @property
    def center(self) -> Tuple[float, float]:
        return (self.x + self.w / 2.0, self.y + self.h / 2.0)

    @property
    def rect(self) -> Tuple[float, float, float, float]:
        return (self.x, self.y, self.w, self.h)


_EXPLICIT_LABEL_RE = re.compile(r"[\(\[]?\s*Q\s*([0-9]{1,2})\s*[\)\].,:;]?", re.I)
_EXPLICIT_TOKEN_RE = re.compile(r"^[\(\[]?\s*Q\s*([0-9]{1,2})\s*[\)\].,:;]?$", re.I)
_Q_ONLY_TOKEN_RE = re.compile(r"^[\(\[]?\s*Q\s*[\)\].,:;]?$", re.I)
_IMPLICIT_TOKEN_RE = re.compile(r"^[\(\[]?\s*([0-9]{1,2})\s*[\)\].,:;]?$")
_ANSWER_REM_RE = re.compile(r"^\d+\s*[Rr]\s*\d+$")
_ANSWER_NUMERIC_RE = re.compile(r"^\d+(?:\.\d+)?(?:/\d+)?$")
_ANSWER_ANY_DIGIT_RE = re.compile(r"\d")
_DIVISION_EXPRESSION_RE = re.compile(r"^\d+\)\d+$")
_EXPLICIT_COVERAGE_THRESHOLD = 0.95
_MIN_ANSWER_TEXT_SCORE = 50
_ANCHOR_FALLBACK_DISQUALIFY_CODES = {
    "ANCHOR_AMBIGUITY_HIGH",
    "ANCHOR_DUPLICATE_NUMBERS",
    "ANCHOR_NUMBER_FALLBACK_ORDER",
    "ANCHOR_OUT_OF_RANGE_NUMBERS",
    "ANCHOR_HARD_GATE_RELAXED",
}


def _anchor_trace_key(anchor: _Anchor) -> str:
    return (
        f"{anchor.page_index}:{anchor.line_index}:{anchor.x:.3f}:{anchor.y:.3f}:"
        f"{anchor.w:.3f}:{anchor.h:.3f}:{anchor.label_method}:{anchor.parsed_num}:{anchor.text}"
    )


def _anchor_bbox_list(anchor: _Anchor) -> List[float]:
    return [float(anchor.x), float(anchor.y), float(anchor.w), float(anchor.h)]


def _warning_codes(warnings: List[Dict[str, object]]) -> set[str]:
    out: set[str] = set()
    for item in warnings or []:
        if not isinstance(item, dict):
            continue
        code = str(item.get("code") or "").strip().upper()
        if code:
            out.add(code)
    return out


def _anchor_fallback_is_reliable(anchor_warnings: List[Dict[str, object]]) -> bool:
    codes = _warning_codes(anchor_warnings)
    return not bool(codes.intersection(_ANCHOR_FALLBACK_DISQUALIFY_CODES))


def _as_float(value: object) -> Optional[float]:
    try:
        out = float(value)
    except Exception:
        return None
    if not math.isfinite(out):
        return None
    return out


def _rect_from_bbox(bounding_box: object) -> Optional[Tuple[float, float, float, float]]:
    if not isinstance(bounding_box, (list, tuple)) or len(bounding_box) < 8:
        return None
    vals = []
    for raw in bounding_box[:8]:
        v = _as_float(raw)
        if v is None:
            return None
        vals.append(v)
    xs = vals[0::2]
    ys = vals[1::2]
    x0 = min(xs)
    y0 = min(ys)
    x1 = max(xs)
    y1 = max(ys)
    w = x1 - x0
    h = y1 - y0
    if w <= 0 or h <= 0:
        return None
    return (x0, y0, w, h)


def _union_rect(rects: List[Tuple[float, float, float, float]]) -> Optional[Tuple[float, float, float, float]]:
    if not rects:
        return None
    x0 = min(r[0] for r in rects)
    y0 = min(r[1] for r in rects)
    x1 = max(r[0] + r[2] for r in rects)
    y1 = max(r[1] + r[3] for r in rects)
    w = x1 - x0
    h = y1 - y0
    if w <= 0 or h <= 0:
        return None
    return (x0, y0, w, h)


def _expand_and_clip(
    rect: Tuple[float, float, float, float],
    *,
    pad_x: float,
    pad_y: float,
    page_w: float,
    page_h: float,
) -> Tuple[float, float, float, float]:
    x, y, w, h = rect
    x0 = max(0.0, x - pad_x)
    y0 = max(0.0, y - pad_y)
    x1 = min(page_w, x + w + pad_x)
    y1 = min(page_h, y + h + pad_y)
    return (x0, y0, max(1.0, x1 - x0), max(1.0, y1 - y0))


def _distance_sq(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    dx = a[0] - b[0]
    dy = a[1] - b[1]
    return dx * dx + dy * dy


def _rect_iou(a: Tuple[float, float, float, float], b: Tuple[float, float, float, float]) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    ax1, ay1 = ax + aw, ay + ah
    bx1, by1 = bx + bw, by + bh
    inter_w = max(0.0, min(ax1, bx1) - max(ax, bx))
    inter_h = max(0.0, min(ay1, by1) - max(ay, by))
    inter = inter_w * inter_h
    if inter <= 0:
        return 0.0
    union = (aw * ah) + (bw * bh) - inter
    return inter / union if union > 0 else 0.0


def _extract_tokens_and_page_sizes(
    ocr_boxes: object,
    image_size: Tuple[int, int],
) -> Tuple[List[_Token], Dict[int, Tuple[float, float]]]:
    tokens: List[_Token] = []
    page_sizes: Dict[int, Tuple[float, float]] = {}
    if not isinstance(ocr_boxes, dict):
        return tokens, page_sizes
    analyze = ocr_boxes.get("analyzeResult") or {}
    read_results = analyze.get("readResults") or []
    if not isinstance(read_results, list):
        return tokens, page_sizes

    for page_index, page in enumerate(read_results):
        if not isinstance(page, dict):
            continue
        pw = _as_float(page.get("width")) or 0.0
        ph = _as_float(page.get("height")) or 0.0
        if pw > 0 and ph > 0:
            page_sizes[page_index] = (pw, ph)
        elif page_index == 0 and image_size[0] > 0 and image_size[1] > 0:
            page_sizes[page_index] = (float(image_size[0]), float(image_size[1]))

        lines = page.get("lines") or []
        if not isinstance(lines, list):
            continue
        for line_index, line in enumerate(lines):
            if not isinstance(line, dict):
                continue
            words = line.get("words") or []
            if isinstance(words, list) and words:
                for word_index, word in enumerate(words):
                    if not isinstance(word, dict):
                        continue
                    text = str(word.get("text") or "").strip()
                    rect = _rect_from_bbox(word.get("boundingBox"))
                    if not text or not rect:
                        continue
                    x, y, w, h = rect
                    tokens.append(
                        _Token(
                            text=text,
                            page_index=page_index,
                            line_index=line_index,
                            word_index=word_index,
                            x=x,
                            y=y,
                            w=w,
                            h=h,
                        )
                    )
            else:
                text = str(line.get("text") or "").strip()
                rect = _rect_from_bbox(line.get("boundingBox"))
                if not text or not rect:
                    continue
                x, y, w, h = rect
                tokens.append(
                    _Token(
                        text=text,
                        page_index=page_index,
                        line_index=line_index,
                        word_index=0,
                        x=x,
                        y=y,
                        w=w,
                        h=h,
                    )
                )

    if 0 not in page_sizes and image_size[0] > 0 and image_size[1] > 0:
        page_sizes[0] = (float(image_size[0]), float(image_size[1]))
    return tokens, page_sizes


def _median_word_height_by_page(tokens: List[_Token]) -> Dict[int, float]:
    by_page: Dict[int, List[float]] = {}
    for tok in tokens:
        if tok.h > 0:
            by_page.setdefault(tok.page_index, []).append(tok.h)
    out: Dict[int, float] = {}
    for page_index, values in by_page.items():
        values = sorted(values)
        mid = len(values) // 2
        if len(values) % 2 == 1:
            out[page_index] = values[mid]
        else:
            out[page_index] = (values[mid - 1] + values[mid]) / 2.0
    return out


def _candidate_from_token(token: _Token, page_w: float) -> Optional[_Anchor]:
    text = token.text.strip()
    if not text:
        return None

    m_explicit = _EXPLICIT_TOKEN_RE.match(text)
    if m_explicit:
        return _Anchor(
            page_index=token.page_index,
            line_index=token.line_index,
            x=token.x,
            y=token.y,
            w=token.w,
            h=token.h,
            parsed_num=int(m_explicit.group(1)),
            confidence=5,
            label_method="ocr_anchor_explicit",
            text=text,
        )

    if _Q_ONLY_TOKEN_RE.match(text):
        return _Anchor(
            page_index=token.page_index,
            line_index=token.line_index,
            x=token.x,
            y=token.y,
            w=token.w,
            h=token.h,
            parsed_num=None,
            confidence=1,
            label_method="ocr_anchor_unknown",
            text=text,
        )

    m_implicit = _IMPLICIT_TOKEN_RE.match(text)
    if m_implicit:
        left_margin = token.x <= max(120.0, page_w * 0.28)
        if left_margin:
            return _Anchor(
                page_index=token.page_index,
                line_index=token.line_index,
                x=token.x,
                y=token.y,
                w=token.w,
                h=token.h,
                parsed_num=int(m_implicit.group(1)),
                confidence=2,
                label_method="ocr_anchor_implicit",
                text=text,
            )
    return None


def _pair_q_tokens_with_number_tokens(
    tokens: List[_Token],
    median_h_by_page: Dict[int, float],
) -> List[_Anchor]:
    by_page: Dict[int, List[_Token]] = {}
    for tok in tokens:
        by_page.setdefault(tok.page_index, []).append(tok)
    out: List[_Anchor] = []
    for page_index, page_tokens in by_page.items():
        h = max(8.0, float(median_h_by_page.get(page_index) or 24.0))
        q_tokens = [t for t in page_tokens if _Q_ONLY_TOKEN_RE.match(t.text.strip())]
        num_tokens = [t for t in page_tokens if _IMPLICIT_TOKEN_RE.match(t.text.strip())]
        for q in sorted(q_tokens, key=lambda t: (t.y, t.x, t.word_index)):
            best = None
            best_score = None
            qx, qy = q.center
            for n in num_tokens:
                nx, ny = n.center
                dx = nx - qx
                dy = abs(ny - qy)
                if dy > (1.4 * h):
                    continue
                if dx < (-0.7 * h) or dx > (5.0 * h):
                    continue
                score = abs(dx) + dy * 2.0
                if best_score is None or score < best_score or (
                    score == best_score and (n.y, n.x, n.word_index) < (best.y, best.x, best.word_index)  # type: ignore[union-attr]
                ):
                    best = n
                    best_score = score
            if not best:
                continue
            match = _IMPLICIT_TOKEN_RE.match(best.text.strip())
            if not match:
                continue
            pair_rect = _union_rect([q.rect, best.rect])
            if not pair_rect:
                continue
            out.append(
                _Anchor(
                    page_index=page_index,
                    line_index=min(q.line_index, best.line_index),
                    x=pair_rect[0],
                    y=pair_rect[1],
                    w=pair_rect[2],
                    h=pair_rect[3],
                    parsed_num=int(match.group(1)),
                    confidence=4,
                    label_method="ocr_anchor_pair",
                    text=f"{q.text} {best.text}",
                )
            )
    return out


def _is_better_anchor(candidate: _Anchor, current: _Anchor) -> bool:
    if candidate.confidence != current.confidence:
        return candidate.confidence > current.confidence
    if (candidate.y, candidate.x) != (current.y, current.x):
        return (candidate.y, candidate.x) < (current.y, current.x)
    return (candidate.w * candidate.h) < (current.w * current.h)


def _dedupe_anchor_candidates(
    candidates: List[_Anchor],
    median_h_by_page: Dict[int, float],
) -> List[_Anchor]:
    kept: List[_Anchor] = []
    ordered = sorted(candidates, key=lambda a: (-a.confidence, a.page_index, a.y, a.x, a.text))
    for cand in ordered:
        page_h = max(8.0, float(median_h_by_page.get(cand.page_index) or 24.0))
        center_thresh_sq = (1.2 * page_h) * (1.2 * page_h)
        matched_idx = None
        for idx, cur in enumerate(kept):
            if cur.page_index != cand.page_index:
                continue
            if _rect_iou(cand.rect, cur.rect) >= 0.30 or _distance_sq(cand.center, cur.center) <= center_thresh_sq:
                matched_idx = idx
                break
        if matched_idx is None:
            kept.append(cand)
            continue
        if _is_better_anchor(cand, kept[matched_idx]):
            kept[matched_idx] = cand
    kept.sort(key=lambda a: (a.page_index, a.y, a.x))
    return kept


def _detect_anchors(
    tokens: List[_Token],
    page_sizes: Dict[int, Tuple[float, float]],
    *,
    include_debug: bool = False,
) -> Any:
    if not tokens:
        return ([], {"candidates": [], "median_h_by_page": {}}) if include_debug else []
    median_h_by_page = _median_word_height_by_page(tokens)
    candidates: List[_Anchor] = []
    for tok in tokens:
        page_w = (page_sizes.get(tok.page_index) or (0.0, 0.0))[0]
        cand = _candidate_from_token(tok, page_w)
        if cand:
            candidates.append(cand)
    candidates.extend(_pair_q_tokens_with_number_tokens(tokens, median_h_by_page))
    if not candidates:
        return ([], {"candidates": [], "median_h_by_page": median_h_by_page}) if include_debug else []
    deduped = _dedupe_anchor_candidates(candidates, median_h_by_page)
    if include_debug:
        return deduped, {"candidates": candidates, "median_h_by_page": median_h_by_page}
    return deduped


def _extract_explicit_numbers_from_tokens(tokens: List[_Token]) -> List[int]:
    nums: List[int] = []
    by_page: Dict[int, List[_Token]] = {}
    for tok in tokens:
        text = tok.text.strip()
        if not text:
            continue
        m_explicit = _EXPLICIT_TOKEN_RE.match(text)
        if m_explicit:
            nums.append(int(m_explicit.group(1)))
        by_page.setdefault(tok.page_index, []).append(tok)

    median_h_by_page = _median_word_height_by_page(tokens)
    pair_anchors = _pair_q_tokens_with_number_tokens(tokens, median_h_by_page)
    for anchor in pair_anchors:
        if anchor.parsed_num is not None:
            nums.append(int(anchor.parsed_num))
    return sorted(set(nums))


def _anchor_overlap(a: _Anchor, b: _Anchor, h_ref: float) -> bool:
    if a.page_index != b.page_index:
        return False
    if _rect_iou(a.rect, b.rect) >= 0.2:
        return True
    return _distance_sq(a.center, b.center) <= (1.3 * h_ref) * (1.3 * h_ref)


def _select_manifest_anchors(
    anchors: List[_Anchor],
    tokens: List[_Token],
    *,
    page_sizes: Optional[Dict[int, Tuple[float, float]]] = None,
) -> Tuple[Dict[int, _Anchor], List[Dict[str, object]], Dict[str, object]]:
    warnings: List[Dict[str, object]] = []
    trace: Dict[str, object] = {
        "expected_max_explicit": None,
        "explicit_numbers_seen": [],
        "covered_count": 0,
        "coverage": None,
        "missing_explicit": [],
        "anchor_decisions": {},
        "missing_detail": [],
    }
    if not anchors:
        return {}, warnings, trace

    explicit_numbers_seen = _extract_explicit_numbers_from_tokens(tokens)
    trace["explicit_numbers_seen"] = list(explicit_numbers_seen)
    median_h_by_page = _median_word_height_by_page(tokens)
    token_max_by_page: Dict[int, Tuple[float, float]] = {}
    for tok in tokens:
        x1 = tok.x + tok.w
        y1 = tok.y + tok.h
        prev = token_max_by_page.get(tok.page_index) or (0.0, 0.0)
        token_max_by_page[tok.page_index] = (max(prev[0], x1), max(prev[1], y1))

    explicit = [
        a
        for a in anchors
        if a.parsed_num is not None and a.label_method in {"ocr_anchor_explicit", "ocr_anchor_pair"}
    ]
    implicit = [
        a
        for a in anchors
        if a.parsed_num is not None and a.label_method == "ocr_anchor_implicit"
    ]

    selected: Dict[int, _Anchor] = {}
    if explicit:
        explicit.sort(key=lambda a: (a.page_index, a.y, a.x))
        decisions: Dict[int, str] = {}
        for anchor in explicit:
            num = int(anchor.parsed_num or 0)
            if num <= 0:
                decisions[id(anchor)] = "REJECTED_INVALID_NUMBER"
                continue
            prev = selected.get(num)
            if prev is None:
                selected[num] = anchor
                decisions[id(anchor)] = "SELECTED_EXPLICIT"
                continue
            if _is_better_anchor(anchor, prev):
                decisions[id(prev)] = "REJECTED_EXPLICIT_WORSE_DUPLICATE"
                selected[num] = anchor
                decisions[id(anchor)] = "SELECTED_EXPLICIT_REPLACED"
            else:
                decisions[id(anchor)] = "REJECTED_EXPLICIT_WORSE_DUPLICATE"

        max_explicit = max(selected.keys()) if selected else 0
        trace["expected_max_explicit"] = max_explicit if max_explicit > 0 else None
        row_pitch_by_page: Dict[int, float] = {}
        by_page_num: Dict[int, List[Tuple[int, _Anchor]]] = {}
        for num, anchor in selected.items():
            by_page_num.setdefault(anchor.page_index, []).append((int(num), anchor))
        for page_index, pairs in by_page_num.items():
            pairs.sort(key=lambda item: item[0])
            deltas: List[float] = []
            for i in range(1, len(pairs)):
                dy = pairs[i][1].y - pairs[i - 1][1].y
                if dy > 0:
                    deltas.append(float(dy))
            if deltas:
                deltas.sort()
                mid = len(deltas) // 2
                if len(deltas) % 2 == 1:
                    row_pitch_by_page[page_index] = deltas[mid]
                else:
                    row_pitch_by_page[page_index] = (deltas[mid - 1] + deltas[mid]) / 2.0

        def _expected_y_for_missing(page_index: int, missing_num: int) -> Optional[float]:
            page_selected = [
                (int(num), anchor)
                for num, anchor in selected.items()
                if anchor.page_index == page_index
            ]
            if not page_selected:
                return None
            page_selected.sort(key=lambda item: item[0])
            prev = None
            nxt = None
            for num, anchor in page_selected:
                if num < missing_num:
                    prev = (num, anchor)
                    continue
                if num > missing_num and nxt is None:
                    nxt = (num, anchor)
            pitch = float(row_pitch_by_page.get(page_index) or 0.0)
            if prev and nxt:
                lo_num, lo_anchor = prev
                hi_num, hi_anchor = nxt
                span = max(1, hi_num - lo_num)
                frac = float(missing_num - lo_num) / float(span)
                return float(lo_anchor.y + frac * (hi_anchor.y - lo_anchor.y))
            if prev and pitch > 0:
                return float(prev[1].y + pitch * float(missing_num - prev[0]))
            if nxt and pitch > 0:
                return float(nxt[1].y - pitch * float(nxt[0] - missing_num))
            if prev:
                return float(prev[1].y)
            if nxt:
                return float(nxt[1].y)
            return None

        for missing in range(1, max_explicit + 1):
            if missing in selected:
                continue
            candidates = [
                a for a in implicit if int(a.parsed_num or 0) == missing and a.confidence >= 2
            ]
            candidates.sort(key=lambda a: (a.page_index, a.y, a.x))
            missing_attempts: List[Dict[str, object]] = []
            for cand in candidates:
                page_w = 0.0
                page_h = 0.0
                if isinstance(page_sizes, dict):
                    page_w, page_h = page_sizes.get(cand.page_index) or (0.0, 0.0)
                token_w, token_h = token_max_by_page.get(cand.page_index) or (0.0, 0.0)
                if page_w <= 0:
                    page_w = token_w
                if page_h <= 0:
                    page_h = token_h
                left_margin_limit = max(140.0, page_w * 0.30) if page_w > 0 else 140.0
                if cand.x > left_margin_limit:
                    decisions[id(cand)] = "REJECTED_IMPLICIT_OUTSIDE_LEFT_MARGIN"
                    missing_attempts.append(
                        {
                            "anchor_key": _anchor_trace_key(cand),
                            "reason": "REJECTED_IMPLICIT_OUTSIDE_LEFT_MARGIN",
                        }
                    )
                    continue

                h_ref = max(8.0, float(median_h_by_page.get(cand.page_index) or cand.h or 24.0))
                expected_y = _expected_y_for_missing(cand.page_index, missing)
                row_pitch = float(row_pitch_by_page.get(cand.page_index) or (3.2 * h_ref))
                band_floor = 2.0 * h_ref
                band_ceil = (0.12 * page_h) if page_h > 0 else (6.0 * h_ref)
                band_target = 0.55 * row_pitch
                band = max(band_floor, min(band_target, band_ceil))
                if expected_y is not None:
                    if abs(cand.center[1] - expected_y) > band:
                        decisions[id(cand)] = "REJECTED_IMPLICIT_OUTSIDE_EXPECTED_BAND"
                        missing_attempts.append(
                            {
                                "anchor_key": _anchor_trace_key(cand),
                                "reason": "REJECTED_IMPLICIT_OUTSIDE_EXPECTED_BAND",
                            }
                        )
                        continue

                if any(_anchor_overlap(cand, taken, h_ref) for taken in selected.values()):
                    decisions[id(cand)] = "REJECTED_IMPLICIT_OVERLAP_WITH_SELECTED"
                    missing_attempts.append(
                        {
                            "anchor_key": _anchor_trace_key(cand),
                            "reason": "REJECTED_IMPLICIT_OVERLAP_WITH_SELECTED",
                        }
                    )
                    continue
                selected[missing] = cand
                decisions[id(cand)] = "SELECTED_IMPLICIT_FILL"
                missing_attempts.append(
                    {
                        "anchor_key": _anchor_trace_key(cand),
                        "reason": "SELECTED_IMPLICIT_FILL",
                    }
                )
                break
            if missing not in selected:
                trace_missing = trace.get("missing_detail")
                if isinstance(trace_missing, list):
                    trace_missing.append(
                        {
                            "question_id": f"Q{missing}",
                            "status": "MISSING",
                            "candidates": missing_attempts,
                        }
                    )

        # If an anchor has an unreadable number (e.g. OCR sees only "Q"),
        # keep it instead of dropping the whole row. Assign by reading order.
        unknown = [
            a
            for a in anchors
            if a.parsed_num is None and a.label_method == "ocr_anchor_unknown"
        ]
        unknown.sort(key=lambda a: (a.page_index, a.y, a.x))

        def _left_neighbor_number(cand: _Anchor) -> Optional[int]:
            h_ref = max(8.0, float(median_h_by_page.get(cand.page_index) or cand.h or 24.0))
            row_band = max(20.0, 1.4 * h_ref)
            best_num: Optional[int] = None
            best_dx: Optional[float] = None
            for num, other in selected.items():
                if other.page_index != cand.page_index:
                    continue
                dy = abs(other.center[1] - cand.center[1])
                if dy > row_band:
                    continue
                dx = cand.center[0] - other.center[0]
                if dx <= (0.4 * h_ref):
                    continue
                if best_dx is None or dx < best_dx:
                    best_dx = dx
                    best_num = int(num)
            return best_num

        unknown_fills = 0
        for cand in unknown:
            h_ref = max(8.0, float(median_h_by_page.get(cand.page_index) or cand.h or 24.0))
            if any(_anchor_overlap(cand, taken, h_ref) for taken in selected.values()):
                decisions[id(cand)] = "REJECTED_UNKNOWN_OVERLAP_WITH_SELECTED"
                continue

            left_num = _left_neighbor_number(cand)
            chosen_num: Optional[int] = None
            if left_num is not None and (left_num + 1) not in selected:
                chosen_num = left_num + 1
            if chosen_num is None:
                max_existing = max(selected.keys()) if selected else 0
                target_max = max(max_existing, max_existing + 1)
                missing_pool = [n for n in range(1, target_max + 1) if n not in selected]
                if missing_pool:
                    chosen_num = missing_pool[0]
                else:
                    chosen_num = max_existing + 1

            if chosen_num in selected:
                decisions[id(cand)] = "REJECTED_UNKNOWN_NUMBER_CONFLICT"
                continue
            selected[int(chosen_num)] = cand
            decisions[id(cand)] = "SELECTED_UNKNOWN_FILL"
            unknown_fills += 1
        if unknown_fills > 0:
            warnings.append(
                {
                    "code": "ANCHOR_UNKNOWN_NUMBER_FILL",
                    "count": unknown_fills,
                    "message": "Assigned question numbers to unreadable Q anchors by reading order.",
                }
            )

        if explicit_numbers_seen:
            max_seen = max(explicit_numbers_seen)
            covered = len([n for n in selected.keys() if 1 <= n <= max_seen])
            coverage = float(covered) / float(max_seen) if max_seen > 0 else 0.0
            trace["expected_max_explicit"] = max_seen if max_seen > 0 else trace.get("expected_max_explicit")
            trace["covered_count"] = covered
            trace["coverage"] = round(coverage, 4)
            missing_explicit = sorted(set(explicit_numbers_seen) - set(selected.keys()))
            trace["missing_explicit"] = list(missing_explicit)
            if missing_explicit:
                warnings.append(
                    {
                        "code": "ANCHOR_EXPLICIT_MISSING_FROM_MANIFEST",
                        "numbers": missing_explicit,
                        "message": f"Explicit OCR anchors missing from manifest: {', '.join(f'Q{n}' for n in missing_explicit)}.",
                    }
                )
            if coverage < _EXPLICIT_COVERAGE_THRESHOLD:
                warnings.append(
                    {
                        "code": "ANCHOR_COVERAGE_BELOW_THRESHOLD",
                        "coverage": round(coverage, 4),
                        "threshold": _EXPLICIT_COVERAGE_THRESHOLD,
                        "max_explicit": max_seen,
                        "covered_count": covered,
                        "message": (
                            f"Explicit anchor coverage {covered}/{max_seen} is below threshold "
                            f"{_EXPLICIT_COVERAGE_THRESHOLD:.2f}."
                        ),
                    }
                )
        for anchor in implicit:
            if id(anchor) not in decisions:
                decisions[id(anchor)] = "REJECTED_IMPLICIT_NOT_REQUIRED"
        for anchor in anchors:
            if anchor.parsed_num is None and id(anchor) not in decisions:
                decisions[id(anchor)] = "REJECTED_NO_PARSED_NUMBER"
        trace["anchor_decisions"] = decisions
    else:
        numbers = _assign_anchor_numbers(anchors)
        decisions = {}
        for idx, anchor in enumerate(anchors):
            selected[int(numbers[idx])] = anchor
            decisions[id(anchor)] = "SELECTED_FALLBACK_ORDER"
        trace["anchor_decisions"] = decisions

    return dict(sorted(selected.items())), warnings, trace


def _assign_anchor_numbers(anchors: List[_Anchor]) -> Dict[int, int]:
    count = len(anchors)
    anchors_by_order = list(enumerate(anchors))
    pinned: Dict[int, Tuple[int, _Anchor]] = {}
    for idx, anchor in anchors_by_order:
        num = anchor.parsed_num
        if num is None or num < 1 or num > count:
            continue
        prev = pinned.get(num)
        if prev is None:
            pinned[num] = (idx, anchor)
            continue
        prev_idx, prev_anchor = prev
        better = (anchor.confidence > prev_anchor.confidence) or (
            anchor.confidence == prev_anchor.confidence
            and (anchor.page_index, anchor.y, anchor.x, idx) < (prev_anchor.page_index, prev_anchor.y, prev_anchor.x, prev_idx)
        )
        if better:
            pinned[num] = (idx, anchor)

    anchor_to_number: Dict[int, int] = {}
    for num in sorted(pinned.keys()):
        anchor_to_number[pinned[num][0]] = num

    remaining_numbers = [n for n in range(1, count + 1) if n not in set(anchor_to_number.values())]
    next_idx = 0
    for idx, _anchor in anchors_by_order:
        if idx in anchor_to_number:
            continue
        anchor_to_number[idx] = remaining_numbers[next_idx]
        next_idx += 1
    return anchor_to_number


def _assign_tokens_to_anchors(tokens: List[_Token], anchors: List[_Anchor]) -> Dict[int, List[_Token]]:
    assigned: Dict[int, List[_Token]] = {idx: [] for idx in range(len(anchors))}
    anchors_by_page: Dict[int, List[Tuple[int, _Anchor]]] = {}
    for idx, anchor in enumerate(anchors):
        anchors_by_page.setdefault(anchor.page_index, []).append((idx, anchor))

    for tok in tokens:
        page_anchors = anchors_by_page.get(tok.page_index) or []
        if not page_anchors:
            continue
        best_idx = None
        best_score = None
        for anchor_idx, anchor in page_anchors:
            score = _distance_sq(tok.center, anchor.center)
            if best_score is None or score < best_score or (score == best_score and anchor_idx < int(best_idx)):
                best_score = score
                best_idx = anchor_idx
        if best_idx is not None:
            assigned[int(best_idx)].append(tok)
    return assigned


def _overlaps_label(token: _Token, anchor: _Anchor) -> bool:
    tx0, ty0, tw, th = token.rect
    tx1, ty1 = tx0 + tw, ty0 + th
    ax0, ay0, aw, ah = anchor.rect
    ax1, ay1 = ax0 + aw, ay0 + ah
    inter_w = max(0.0, min(tx1, ax1) - max(tx0, ax0))
    inter_h = max(0.0, min(ty1, ay1) - max(ty0, ay0))
    inter = inter_w * inter_h
    if inter <= 0:
        return False
    token_area = max(1.0, tw * th)
    return (inter / token_area) >= 0.4


def _score_answer_text(text: str) -> int:
    normalized = normalize_answer_text(text)
    compact = re.sub(r"\s+", " ", str(normalized or "").strip())
    if not compact:
        return 0
    if re.fullmatch(r"\d+[.,:;]$", compact):
        # Common OCR from circled labels (e.g. "8.") should not be treated as strong answers.
        return 10
    if _looks_like_question_label_text(compact):
        return 0
    if _looks_like_division_expression_text(compact):
        return 0
    # Reject mixed alpha-digit text unless it matches noisy remainder form (e.g., "23RO").
    if re.search(r"[A-Za-z]", compact):
        if not re.fullmatch(r"\d+\s*[Rr]\s*[0-9OoIl|]+", compact):
            return 10
    if _ANSWER_REM_RE.match(compact):
        return 120
    if _ANSWER_NUMERIC_RE.match(compact):
        return 95
    if _ANSWER_ANY_DIGIT_RE.search(compact):
        return 55
    return 5


def _geometry_answer_span_candidates(
    *,
    anchor: _Anchor,
    page_tokens: List[_Token],
    page_size: Tuple[float, float],
    h_ref: float,
) -> List[Dict[str, object]]:
    page_w, page_h = page_size
    ax, ay, aw, ah = anchor.rect
    anchor_cx, anchor_cy = anchor.center
    dx_min = max(10.0, 0.8 * h_ref)
    # Keep horizontal reach bounded, but allow wide worksheets where answers are farther right.
    dx_max = min(page_w * 0.45, 22.0 * h_ref)
    dy_max = min(page_h * 0.07, 1.8 * h_ref)

    def _valid_span_center(cx: float, cy: float) -> Tuple[bool, float, float]:
        dx = cx - anchor_cx
        dy = abs(cy - anchor_cy)
        right_column_floor = anchor_cx + dx_min
        if cx < right_column_floor:
            return False, dx, dy
        if dx < dx_min or dx > dx_max:
            return False, dx, dy
        if dy > dy_max:
            return False, dx, dy
        return True, dx, dy

    spans: List[Dict[str, object]] = []
    for tok in page_tokens:
        if _overlaps_label(tok, anchor):
            continue
        cx, cy = tok.center
        ok, dx, dy = _valid_span_center(cx, cy)
        if not ok:
            continue
        text_score = _score_answer_text(tok.text)
        if text_score < _MIN_ANSWER_TEXT_SCORE:
            continue
        spans.append(
            {
                "rect": tok.rect,
                "text": tok.text,
                "token_ids": [_token_key(tok)],
                "dx": dx,
                "dy": dy,
                "text_score": text_score,
            }
        )

    by_line: Dict[int, List[_Token]] = {}
    for tok in page_tokens:
        if _overlaps_label(tok, anchor):
            continue
        by_line.setdefault(tok.line_index, []).append(tok)
    for line_idx in sorted(by_line.keys()):
        line_tokens = sorted(by_line[line_idx], key=lambda t: (t.x, t.word_index))
        for i in range(len(line_tokens) - 1):
            t1 = line_tokens[i]
            t2 = line_tokens[i + 1]
            if _looks_like_question_label_text(t1.text) or _looks_like_question_label_text(t2.text):
                continue
            rect = _union_rect([t1.rect, t2.rect])
            if not rect:
                continue
            cx = rect[0] + rect[2] / 2.0
            cy = rect[1] + rect[3] / 2.0
            ok, dx, dy = _valid_span_center(cx, cy)
            if not ok:
                continue
            merged = f"{t1.text} {t2.text}".strip()
            merged_score = _score_answer_text(merged)
            if merged_score < _MIN_ANSWER_TEXT_SCORE:
                continue
            spans.append(
                {
                    "rect": rect,
                    "text": merged,
                    "token_ids": [_token_key(t1), _token_key(t2)],
                    "dx": dx,
                    "dy": dy,
                    "text_score": merged_score,
                }
            )
        # Allow non-adjacent numeric + remainder pair joins on noisy OCR lines.
        for i in range(len(line_tokens)):
            t_num = line_tokens[i]
            if not _ANSWER_NUMERIC_RE.fullmatch(str(t_num.text or "").strip()):
                continue
            for j in range(i + 1, len(line_tokens)):
                t_rem = line_tokens[j]
                if not re.fullmatch(r"[Rr]\d+", str(t_rem.text or "").strip()):
                    continue
                if abs(t_rem.center[1] - t_num.center[1]) > (0.9 * h_ref):
                    continue
                gap = float(t_rem.x - (t_num.x + t_num.w))
                if gap < (-0.2 * h_ref) or gap > (8.0 * h_ref):
                    continue
                rect = _union_rect([t_num.rect, t_rem.rect])
                if not rect:
                    continue
                cx = rect[0] + rect[2] / 2.0
                cy = rect[1] + rect[3] / 2.0
                ok, dx, dy = _valid_span_center(cx, cy)
                if not ok:
                    continue
                merged = f"{t_num.text} {t_rem.text}".strip()
                merged_score = _score_answer_text(merged)
                if merged_score < _MIN_ANSWER_TEXT_SCORE:
                    continue
                spans.append(
                    {
                        "rect": rect,
                        "text": merged,
                        "token_ids": [_token_key(t_num), _token_key(t_rem)],
                        "dx": dx,
                        "dy": dy,
                        "text_score": merged_score,
                    }
                )
    spans.sort(
        key=lambda item: (
            int(float(item.get("dx") or 0.0) / max(32.0, h_ref * 1.2)),
            int(float(item.get("dy") or 0.0) / max(12.0, h_ref * 0.6)),
            -int(item.get("text_score") or 0),
            float(item.get("dx") or 0.0),
            float(item.get("dy") or 0.0),
            float((item.get("rect") or [0, 0, 0, 0])[1]),
            float((item.get("rect") or [0, 0, 0, 0])[0]),
        )
    )
    return spans


def _best_geometry_answer_span(
    *,
    anchor: _Anchor,
    page_tokens: List[_Token],
    page_size: Tuple[float, float],
    h_ref: float,
) -> Optional[Dict[str, object]]:
    spans = _geometry_answer_span_candidates(
        anchor=anchor,
        page_tokens=page_tokens,
        page_size=page_size,
        h_ref=h_ref,
    )
    return spans[0] if spans else None


def _token_key(token: _Token) -> str:
    return (
        f"{token.page_index}:{token.line_index}:{token.word_index}:"
        f"{token.x:.3f}:{token.y:.3f}:{token.w:.3f}:{token.h:.3f}:{token.text}"
    )


def _pick_answer_box(
    *,
    anchor: _Anchor,
    anchor_tokens: List[_Token],
    page_tokens: List[_Token],
    page_size: Tuple[float, float],
    region_box: Tuple[float, float, float, float],
    reserved_token_keys: set[str],
    reserved_answer_boxes: List[Tuple[int, Tuple[float, float, float, float]]],
    preferred_span: Optional[Dict[str, object]] = None,
) -> Tuple[Tuple[float, float, float, float], str, List[str]]:
    page_w, page_h = page_size
    ax, ay, aw, ah = anchor.rect
    anchor_cx, anchor_cy = anchor.center
    search_x0 = max(0.0, ax - max(30.0, page_w * 0.04))
    search_x1 = min(page_w, ax + max(240.0, page_w * 0.52, aw * 8.0))
    # Keep answer-span search close to the anchor row; large vertical windows caused cross-row steals.
    search_y0 = max(0.0, ay - max(34.0, ah * 1.2))
    search_y1 = min(page_h, ay + max(120.0, ah * 2.6))
    if anchor_cy >= (page_h * 0.62):
        # Bottom rows are more vulnerable to footer pulls; use a tighter downward window.
        search_y1 = min(search_y1, ay + max(74.0, ah * 1.9))

    def _within_anchor_band(rect: Tuple[float, float, float, float]) -> bool:
        rx, ry, rw, rh = rect
        cx = rx + (rw / 2.0)
        cy = ry + (rh / 2.0)
        dx = cx - anchor_cx
        dy = abs(cy - anchor_cy)
        dx_min = -max(10.0, ah * 0.35)
        dx_max = max(260.0, page_w * 0.52, aw * 8.0)
        dy_max = max(92.0, ah * 2.4)
        if anchor_cy >= (page_h * 0.62):
            dy_max = min(dy_max, max(60.0, ah * 1.55))
        return (dx_min <= dx <= dx_max) and (dy <= dy_max)

    nearby = []
    for tok in page_tokens:
        tx, ty, tw, th = tok.rect
        cx, cy = tok.center
        if cx < search_x0 or cx > search_x1 or cy < search_y0 or cy > search_y1:
            continue
        if _overlaps_label(tok, anchor):
            continue
        nearby.append(tok)
    nearby.sort(key=lambda t: (t.line_index, t.x, t.word_index))

    if isinstance(preferred_span, dict):
        pref_rect = preferred_span.get("rect")
        pref_text = str(preferred_span.get("text") or "")
        pref_token_ids = preferred_span.get("token_ids") if isinstance(preferred_span.get("token_ids"), list) else []
        if isinstance(pref_rect, (list, tuple)) and len(pref_rect) >= 4:
            clipped_pref = _expand_and_clip(
                (float(pref_rect[0]), float(pref_rect[1]), float(pref_rect[2]), float(pref_rect[3])),
                pad_x=10.0,
                pad_y=8.0,
                page_w=page_w,
                page_h=page_h,
            )
            pref_overlap = False
            for page_idx, reserved_rect in reserved_answer_boxes:
                if page_idx != anchor.page_index:
                    continue
                if _rect_iou(clipped_pref, reserved_rect) > 0.18:
                    pref_overlap = True
                    break
            if not pref_overlap:
                if all(token_id not in reserved_token_keys for token_id in pref_token_ids):
                    if _within_anchor_band(clipped_pref):
                        return clipped_pref, normalize_answer_text(pref_text), list(pref_token_ids)

    candidates: List[Tuple[float, float, float, Tuple[float, float, float, float], str, List[str]]] = []

    for tok in nearby:
        token_id = _token_key(tok)
        if token_id in reserved_token_keys:
            continue
        base = _score_answer_text(tok.text)
        if base < _MIN_ANSWER_TEXT_SCORE:
            continue
        cx, cy = tok.center
        dx = max(0.0, cx - anchor_cx)
        dy = abs(cy - anchor_cy)
        candidates.append((dx, dy, -float(base), tok.rect, tok.text, [token_id]))

    by_line: Dict[int, List[_Token]] = {}
    for tok in nearby:
        by_line.setdefault(tok.line_index, []).append(tok)
    for line_idx in sorted(by_line.keys()):
        line_tokens = sorted(by_line[line_idx], key=lambda t: (t.x, t.word_index))
        for i in range(len(line_tokens) - 1):
            t1 = line_tokens[i]
            t2 = line_tokens[i + 1]
            t1_id = _token_key(t1)
            t2_id = _token_key(t2)
            if t1_id in reserved_token_keys or t2_id in reserved_token_keys:
                continue
            if _looks_like_question_label_text(t1.text) or _looks_like_question_label_text(t2.text):
                continue
            merged = f"{t1.text} {t2.text}".strip()
            base = _score_answer_text(merged)
            if base < _MIN_ANSWER_TEXT_SCORE:
                continue
            rect = _union_rect([t1.rect, t2.rect])
            if not rect:
                continue
            cx = rect[0] + rect[2] / 2.0
            cy = rect[1] + rect[3] / 2.0
            dx = max(0.0, cx - anchor_cx)
            dy = abs(cy - anchor_cy)
            candidates.append((dx, dy, -float(base + 8), rect, merged, [t1_id, t2_id]))

    if candidates:
        bucket_x = max(28.0, ah * 1.2)
        bucket_y = max(10.0, ah * 0.7)
        candidates.sort(
            key=lambda item: (
                int(item[0] / bucket_x),
                int(item[1] / bucket_y),
                item[2],
                item[0],
                item[1],
                item[3][1],
                item[3][0],
            )
        )
        for _dx, _dy, _neg_score, rect, text, token_ids in candidates:
            clipped = _expand_and_clip(rect, pad_x=10.0, pad_y=8.0, page_w=page_w, page_h=page_h)
            if not _within_anchor_band(clipped):
                continue
            overlaps = False
            for page_idx, reserved_rect in reserved_answer_boxes:
                if page_idx != anchor.page_index:
                    continue
                if _rect_iou(clipped, reserved_rect) > 0.18:
                    overlaps = True
                    break
            if overlaps:
                continue
            return clipped, normalize_answer_text(text), token_ids

    fallback_pool = [t for t in anchor_tokens if not _overlaps_label(t, anchor)]
    if fallback_pool:
        fallback_pool = [t for t in fallback_pool if _token_key(t) not in reserved_token_keys]
    if fallback_pool:
        fallback_pool = [t for t in fallback_pool if _within_anchor_band(t.rect)]
        scored_pool = [
            (max(0, _score_answer_text(t.text)), _distance_sq(t.center, anchor.center), t)
            for t in fallback_pool
        ]
        strong = [item for item in scored_pool if item[0] >= _MIN_ANSWER_TEXT_SCORE]
        if strong:
            strong.sort(key=lambda item: (-item[0], item[1], item[2].y, item[2].x))
            tok = strong[0][2]
            rect = _expand_and_clip(tok.rect, pad_x=10.0, pad_y=8.0, page_w=page_w, page_h=page_h)
            if _within_anchor_band(rect):
                return rect, normalize_answer_text(tok.text), [_token_key(tok)]

    rx, ry, rw, rh = region_box
    fallback_w = max(24.0, min(max(42.0, aw * 2.0), page_w * 0.16))
    fallback_h = max(20.0, min(max(24.0, ah * 1.05), page_h * 0.07))
    fallback = (
        min(page_w - 1.0, max(0.0, ax + aw + 20.0)),
        min(page_h - 1.0, max(0.0, ay + max(6.0, ah * 0.2))),
        fallback_w,
        fallback_h,
    )
    return _expand_and_clip(fallback, pad_x=0.0, pad_y=0.0, page_w=page_w, page_h=page_h), "", []


def _rect_center_in(rect: Tuple[float, float, float, float], box: Tuple[float, float, float, float]) -> bool:
    x, y, w, h = rect
    bx, by, bw, bh = box
    cx = x + (w / 2.0)
    cy = y + (h / 2.0)
    return bx <= cx <= (bx + bw) and by <= cy <= (by + bh)


def _clip_box(
    box: Tuple[float, float, float, float],
    page_w: float,
    page_h: float,
) -> Optional[Tuple[float, float, float, float]]:
    x, y, w, h = box
    if w <= 0 or h <= 0:
        return None
    x0 = max(0.0, x)
    y0 = max(0.0, y)
    x1 = min(page_w, x + w)
    y1 = min(page_h, y + h)
    cw = x1 - x0
    ch = y1 - y0
    if cw <= 1.0 or ch <= 1.0:
        return None
    return (x0, y0, cw, ch)


def _suppress_overlapping_boxes(
    boxes: List[Tuple[float, float, float, float]],
    *,
    iou_threshold: float = 0.22,
) -> Tuple[List[Tuple[float, float, float, float]], int]:
    ordered = sorted(boxes, key=lambda r: (-(r[2] * r[3]), r[1], r[0]))
    kept: List[Tuple[float, float, float, float]] = []
    removed = 0
    for box in ordered:
        if any(_rect_iou(box, other) > iou_threshold for other in kept):
            removed += 1
            continue
        kept.append(box)
    kept.sort(key=lambda r: (r[1], r[0]))
    return kept, removed


def _best_anchor_for_box_row(
    *,
    box: Tuple[float, float, float, float],
    row_tokens: List[_Token],
    page_size: Tuple[float, float],
    reserved_anchor_keys: Optional[set[str]] = None,
) -> Tuple[Optional[_Anchor], List[_Anchor]]:
    page_w, _page_h = page_size
    if not row_tokens:
        return None, []
    h_by_page = _median_word_height_by_page(row_tokens)
    candidates: List[_Anchor] = []
    for tok in row_tokens:
        cand = _candidate_from_token(tok, page_w)
        if cand:
            candidates.append(cand)
    candidates.extend(_pair_q_tokens_with_number_tokens(row_tokens, h_by_page))
    if not candidates:
        return None, []
    deduped = _dedupe_anchor_candidates(candidates, h_by_page)
    parsed = [c for c in deduped if c.parsed_num is not None]
    if not parsed:
        return None, deduped
    box_cy = box[1] + (box[3] / 2.0)
    box_x = box[0]
    row_band = max(24.0, box[3] * 1.4)
    within_band = [c for c in parsed if abs(c.center[1] - box_cy) <= row_band]
    if within_band:
        parsed = within_band
    parsed.sort(
        key=lambda c: (
            abs(c.center[1] - box_cy),
            abs(c.center[0] - box_x),
            -int(c.confidence),
            c.y,
            c.x,
        )
    )
    reserved = reserved_anchor_keys or set()
    for cand in parsed:
        if _anchor_trace_key(cand) in reserved:
            continue
        return cand, deduped
    return None, deduped


def _extract_answer_text_in_box(
    *,
    box: Tuple[float, float, float, float],
    page_tokens: List[_Token],
) -> str:
    hits = [tok for tok in page_tokens if _rect_center_in(tok.rect, box)]
    if not hits:
        return ""
    hits.sort(key=lambda t: (t.line_index, t.x, t.word_index))
    text = " ".join(tok.text for tok in hits if tok.text)
    return normalize_answer_text(text)


def _median(values: List[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2 == 1:
        return float(ordered[mid])
    return float((ordered[mid - 1] + ordered[mid]) / 2.0)


def _question_row_centers_for_box_filter(
    *,
    page_tokens: List[_Token],
    page_size: Tuple[float, float],
) -> List[float]:
    if not page_tokens:
        return []
    page_w, page_h = page_size
    anchors = _detect_anchors(page_tokens, {0: (page_w, page_h)})
    parsed = [a for a in anchors if isinstance(a, _Anchor) and a.parsed_num is not None]
    if not parsed:
        return []
    strong = [a for a in parsed if a.label_method in {"ocr_anchor_explicit", "ocr_anchor_pair"}]
    use = strong or parsed
    ys = sorted(a.center[1] for a in use)
    collapsed: List[float] = []
    for y in ys:
        if not collapsed:
            collapsed.append(float(y))
            continue
        if abs(y - collapsed[-1]) <= 24.0:
            collapsed[-1] = float((collapsed[-1] + y) / 2.0)
        else:
            collapsed.append(float(y))
    return collapsed


def _looks_like_question_label_text(text: str) -> bool:
    compact = re.sub(r"\s+", "", str(text or "")).upper()
    if not compact:
        return False
    compact = compact.lstrip("([")
    compact = compact.rstrip(").,:;]")
    if not compact.startswith("Q"):
        return False
    tail = compact[1:]
    return tail == "" or tail.isdigit()


def _looks_like_division_expression_text(text: str) -> bool:
    compact = re.sub(r"\s+", "", str(text or "")).upper()
    if not compact:
        return False
    return bool(_DIVISION_EXPRESSION_RE.fullmatch(compact))


def _build_box_driven_regions(
    *,
    tokens: List[_Token],
    page_sizes: Dict[int, Tuple[float, float]],
    image_size: Tuple[int, int],
    answer_box_hints: List[Tuple[float, float, float, float]],
) -> Tuple[List[AnchorRegion], List[Dict[str, object]], Dict[str, object]]:
    warnings: List[Dict[str, object]] = []
    page_w = float((page_sizes.get(0) or (float(image_size[0]), float(image_size[1])))[0] or float(image_size[0]) or 0.0)
    page_h = float((page_sizes.get(0) or (float(image_size[0]), float(image_size[1])))[1] or float(image_size[1]) or 0.0)

    raw_boxes: List[Tuple[float, float, float, float]] = []
    for item in answer_box_hints:
        try:
            box = (float(item[0]), float(item[1]), float(item[2]), float(item[3]))
        except Exception:
            continue
        clipped = _clip_box(box, page_w, page_h)
        if clipped:
            raw_boxes.append(clipped)

    if not raw_boxes:
        raise ValueError("template_answer_boxes_missing")

    page_tokens = [tok for tok in tokens if tok.page_index == 0]
    question_rows = _question_row_centers_for_box_filter(
        page_tokens=page_tokens,
        page_size=(page_w, page_h),
    )
    row_band = 0.0
    if len(question_rows) >= 2:
        deltas = [
            question_rows[i] - question_rows[i - 1]
            for i in range(1, len(question_rows))
            if (question_rows[i] - question_rows[i - 1]) > 0
        ]
        row_pitch = _median(deltas)
        if row_pitch > 0:
            row_band = max(42.0, min(180.0, row_pitch * 0.48))
    filtered_candidates: List[Dict[str, object]] = []
    filtered_raw_boxes: List[Tuple[float, float, float, float]] = []
    for box in raw_boxes:
        reasons: List[str] = []
        text_in_box = _extract_answer_text_in_box(box=box, page_tokens=page_tokens)
        is_q_label = _looks_like_question_label_text(text_in_box)
        is_div_expr = _looks_like_division_expression_text(text_in_box)
        has_digit_signal = bool(_ANSWER_ANY_DIGIT_RE.search(text_in_box))
        if is_q_label:
            reasons.append("q_label_text")
        if is_div_expr:
            reasons.append("division_expression_text")
        if row_band > 0.0 and question_rows:
            cy = box[1] + (box[3] / 2.0)
            nearest = min(abs(cy - row_y) for row_y in question_rows)
            if nearest > row_band and not (has_digit_signal and not is_q_label and not is_div_expr):
                reasons.append("far_from_question_rows")
        if reasons:
            filtered_candidates.append(
                {
                    "bbox_px": [float(box[0]), float(box[1]), float(box[2]), float(box[3])],
                    "text": text_in_box,
                    "reasons": reasons,
                }
            )
            continue
        filtered_raw_boxes.append(box)
    if filtered_raw_boxes:
        raw_boxes = filtered_raw_boxes
    if filtered_candidates:
        reason_counts: Dict[str, int] = {}
        for item in filtered_candidates:
            for reason in item.get("reasons", []):
                reason_counts[str(reason)] = reason_counts.get(str(reason), 0) + 1
        warnings.append(
            {
                "code": "BOX_CANDIDATES_FILTERED",
                "filtered_count": len(filtered_candidates),
                "kept_count": len(raw_boxes),
                "reasons": reason_counts,
                "message": "Filtered non-answer-like answer-box candidates before row anchoring.",
            }
        )

    boxes, overlap_removed = _suppress_overlapping_boxes(raw_boxes, iou_threshold=0.22)
    if overlap_removed > 0:
        warnings.append(
            {
                "code": "BOX_OVERLAP_AMBIGUOUS",
                "removed_count": overlap_removed,
                "message": "Detected overlapping answer boxes; review needed.",
            }
        )

    explicit_seen = _extract_explicit_numbers_from_tokens(tokens)
    explicit_max = max(explicit_seen) if explicit_seen else 0
    if explicit_max > 0 and len(boxes) < explicit_max:
        warnings.append(
            {
                "code": "BOX_COUNT_TOO_FEW",
                "box_count": len(boxes),
                "expected_at_least": explicit_max,
                "message": "Detected answer-box count is below explicit OCR label count.",
            }
        )
    if explicit_max > 0 and len(boxes) > explicit_max:
        warnings.append(
            {
                "code": "BOX_COUNT_TOO_MANY",
                "box_count": len(boxes),
                "expected_at_most": explicit_max,
                "message": "Detected answer-box count is above explicit OCR label count.",
            }
        )

    candidate_entries: List[Dict[str, object]] = []
    selected_entries: List[Dict[str, object]] = []
    rejected_entries: List[Dict[str, object]] = []
    row_entries: List[Dict[str, object]] = []
    unreadable_rows: List[int] = []
    row_records: List[Dict[str, object]] = []
    reserved_anchor_keys: set[str] = set()
    row_pitch = 0.0
    if len(boxes) > 1:
        centers = sorted((b[1] + (b[3] / 2.0)) for b in boxes)
        deltas = [centers[i] - centers[i - 1] for i in range(1, len(centers)) if (centers[i] - centers[i - 1]) > 0]
        if deltas:
            deltas.sort()
            mid = len(deltas) // 2
            if len(deltas) % 2 == 1:
                row_pitch = float(deltas[mid])
            else:
                row_pitch = float((deltas[mid - 1] + deltas[mid]) / 2.0)

    for row_index, box in enumerate(boxes, start=1):
        bx, by, bw, bh = box
        left_w = max(220.0, bw * 5.0, bx * 0.85)
        right_gap = max(16.0, bw * 0.35)
        vpad = max(18.0, bh * 1.05)
        if row_pitch > 0:
            vpad = min(vpad, max(18.0, row_pitch * 0.48))
        roi = _clip_box((bx - left_w, by - vpad, max(1.0, left_w - right_gap), bh + (2.0 * vpad)), page_w, page_h)
        if roi is None:
            roi = (0.0, max(0.0, by - vpad), max(1.0, bx - right_gap), bh + (2.0 * vpad))
        row_tokens = [tok for tok in page_tokens if _rect_center_in(tok.rect, roi)]
        reserved_before = set(reserved_anchor_keys)
        best_anchor, row_candidates = _best_anchor_for_box_row(
            box=box,
            row_tokens=row_tokens,
            page_size=(page_w, page_h),
            reserved_anchor_keys=reserved_anchor_keys,
        )

        for cand in row_candidates:
            candidate_entries.append(
                {
                    "text": cand.text,
                    "bbox_px": _anchor_bbox_list(cand),
                    "method": cand.label_method,
                    "parsed_num": cand.parsed_num,
                }
            )

        if best_anchor is None:
            unreadable_rows.append(row_index)
            for cand in row_candidates:
                reason = f"unreadable_q_label_row_{row_index}"
                if _anchor_trace_key(cand) in reserved_before:
                    reason = "anchor_reserved_by_previous_row"
                rejected_entries.append(
                    {
                        "text": cand.text,
                        "bbox_px": _anchor_bbox_list(cand),
                        "method": cand.label_method,
                        "parsed_num": cand.parsed_num,
                        "reason": reason,
                    }
                )
        else:
            reserved_anchor_keys.add(_anchor_trace_key(best_anchor))
            selected_entries.append(
                {
                    "question_id": f"ROW{row_index}",
                    "text": best_anchor.text,
                    "bbox_px": _anchor_bbox_list(best_anchor),
                    "method": best_anchor.label_method,
                    "parsed_num": best_anchor.parsed_num,
                }
            )
            for cand in row_candidates:
                if _anchor_trace_key(cand) == _anchor_trace_key(best_anchor):
                    continue
                reason = f"not_best_for_row_{row_index}"
                if _anchor_trace_key(cand) in reserved_before:
                    reason = "anchor_reserved_by_previous_row"
                rejected_entries.append(
                    {
                        "text": cand.text,
                        "bbox_px": _anchor_bbox_list(cand),
                        "method": cand.label_method,
                        "parsed_num": cand.parsed_num,
                        "reason": reason,
                    }
                )

        row_entries.append(
            {
                "row_index": row_index,
                "box_bbox_px": [float(box[0]), float(box[1]), float(box[2]), float(box[3])],
                "roi_bbox_px": [float(roi[0]), float(roi[1]), float(roi[2]), float(roi[3])],
                "anchor_bbox_px": (
                    _anchor_bbox_list(best_anchor) if isinstance(best_anchor, _Anchor) else None
                ),
                "anchor_text": (best_anchor.text if isinstance(best_anchor, _Anchor) else None),
                "parsed_num": (int(best_anchor.parsed_num) if isinstance(best_anchor, _Anchor) and best_anchor.parsed_num is not None else None),
            }
        )

        row_records.append(
            {
                "row_index": row_index,
                "box": box,
                "roi": roi,
                "anchor": best_anchor,
            }
        )

    parsed_to_rows: Dict[int, List[int]] = {}
    for row in row_records:
        anchor = row.get("anchor")
        if isinstance(anchor, _Anchor) and anchor.parsed_num is not None:
            parsed_to_rows.setdefault(int(anchor.parsed_num), []).append(int(row["row_index"]))
    duplicate_numbers = sorted(num for num, rows in parsed_to_rows.items() if len(rows) > 1)
    if duplicate_numbers:
        warnings.append(
            {
                "code": "BOX_DUPLICATE_Q_NUMBERS",
                "numbers": duplicate_numbers,
                "message": "Duplicate Q numbers detected across answer-box rows.",
            }
        )

    if unreadable_rows:
        warnings.append(
            {
                "code": "BOX_UNREADABLE_Q_LABEL_ROWS",
                "rows": unreadable_rows,
                "message": "Unreadable Q label in one or more answer-box rows.",
            }
        )

    if len(boxes) <= 1 or (len(unreadable_rows) > max(1, len(boxes) // 3)):
        warnings.append(
            {
                "code": "BOX_CONFIDENCE_LOW",
                "box_count": len(boxes),
                "unreadable_rows": len(unreadable_rows),
                "message": "Answer-box detection confidence is low.",
            }
        )

    used_numbers: set[int] = set()
    regions: List[AnchorRegion] = []
    for row in row_records:
        row_index = int(row["row_index"])
        box = row["box"]
        anchor = row.get("anchor")
        qid: str
        label_method = "ocr_anchor_unreadable"
        if isinstance(anchor, _Anchor) and anchor.parsed_num is not None and int(anchor.parsed_num) not in used_numbers:
            used_numbers.add(int(anchor.parsed_num))
            qid = f"Q{int(anchor.parsed_num)}"
            label_method = anchor.label_method
        else:
            qid = f"QROW{row_index}"
        bx, by, bw, bh = box
        region_left_w = max(220.0, bw * 5.0, bx * 0.85)
        region_vpad = max(18.0, bh * 1.05)
        if row_pitch > 0:
            region_vpad = min(region_vpad, max(18.0, row_pitch * 0.48))
        region_box = _expand_and_clip(
            (
                max(0.0, bx - region_left_w),
                max(0.0, by - region_vpad),
                max(120.0, bw + region_left_w),
                bh + (2.0 * region_vpad),
            ),
            pad_x=0.0,
            pad_y=0.0,
            page_w=page_w,
            page_h=page_h,
        )
        expected_text = _extract_answer_text_in_box(box=box, page_tokens=page_tokens)
        regions.append(
            AnchorRegion(
                qid=qid,
                region=region_box,
                answer_box=box,
                expected_answer_text=expected_text,
                label_method=label_method,
                index=row_index,
                page_index=0,
            )
        )

    regions.sort(key=lambda r: (r.page_index, r.region[1], r.region[0], str(r.qid)))

    missing_numbers = [n for n in range(1, len(boxes) + 1) if n not in used_numbers]
    if missing_numbers:
        warnings.append(
            {
                "code": "BOX_MISSING_Q_NUMBERS",
                "numbers": missing_numbers,
                "message": "Missing parsed Q numbers across answer-box rows.",
            }
        )
    fallback_qids = sorted(str(region.qid) for region in regions if str(region.qid).startswith("QROW"))
    if fallback_qids:
        warnings.append(
            {
                "code": "BOX_ROW_FALLBACK_QIDS",
                "qids": fallback_qids,
                "message": "One or more rows fell back to synthetic QROW ids.",
            }
        )
    anchor_trace: Dict[str, object] = {
        "candidates": candidate_entries,
        "selected": selected_entries,
        "rejected": rejected_entries,
        "filtered": filtered_candidates,
        "rows": row_entries,
        "missing_numbers": missing_numbers,
        "summary": {
            "candidate_count": len(candidate_entries),
            "selected_count": len(selected_entries),
            "rejected_count": len(rejected_entries),
            "missing_count": len(missing_numbers),
        },
    }
    return regions, warnings, anchor_trace


def build_anchor_template_regions(
    *,
    ocr_boxes: object,
    image_size: Tuple[int, int],
    answer_box_hints: Optional[List[Tuple[float, float, float, float]]] = None,
) -> Tuple[List[AnchorRegion], List[Dict[str, object]], Dict[str, object]]:
    if answer_box_hints:
        tokens, page_sizes = _extract_tokens_and_page_sizes(ocr_boxes, image_size)
        box_regions, box_warnings, box_trace = _build_box_driven_regions(
            tokens=tokens,
            page_sizes=page_sizes,
            image_size=image_size,
            answer_box_hints=answer_box_hints,
        )
        explicit_seen = _extract_explicit_numbers_from_tokens(tokens)
        explicit_max = max(explicit_seen) if explicit_seen else 0
        summary = (box_trace or {}).get("summary") if isinstance(box_trace, dict) else {}
        box_selected = int((summary or {}).get("selected_count") or 0)
        box_missing = int((summary or {}).get("missing_count") or 0)
        parsed_qids = [str(r.qid) for r in box_regions if str(r.qid).startswith("Q") and not str(r.qid).startswith("QROW")]
        box_parsed_count = len(parsed_qids)
        fallback_needed = False
        if explicit_max > 0:
            min_cover = max(2, int(math.ceil(explicit_max * 0.75)))
            if box_parsed_count < min_cover:
                fallback_needed = True
            if box_selected < min_cover:
                fallback_needed = True
            if box_missing > max(1, explicit_max // 3):
                fallback_needed = True
        if fallback_needed:
            anchor_regions, anchor_warnings, anchor_trace = build_anchor_template_regions(
                ocr_boxes=ocr_boxes,
                image_size=image_size,
                answer_box_hints=None,
            )
            anchor_parsed_count = len(
                [str(r.qid) for r in anchor_regions if str(r.qid).startswith("Q") and not str(r.qid).startswith("QROW")]
            )
            anchor_reliable = _anchor_fallback_is_reliable(anchor_warnings or [])
            if anchor_reliable and ((anchor_parsed_count > box_parsed_count) or (len(anchor_regions) > len(box_regions))):
                fallback_warning = {
                    "code": "BOX_MODE_FALLBACK_APPLIED",
                    "message": "Box-driven extraction had low coverage; anchor-driven fallback selected.",
                    "box_summary": summary or {},
                    "box_regions": len(box_regions),
                    "anchor_regions": len(anchor_regions),
                    "explicit_max": explicit_max,
                }
                warnings = [fallback_warning]
                warnings.extend(anchor_warnings or [])
                if isinstance(anchor_trace, dict):
                    anchor_trace["box_mode_discarded"] = {
                        "summary": summary or {},
                        "regions": len(box_regions),
                        "parsed_qids": parsed_qids,
                    }
                return anchor_regions, warnings, anchor_trace
            if isinstance(box_trace, dict):
                box_trace["anchor_mode_rejected"] = {
                    "reason": "anchor_fallback_not_reliable" if not anchor_reliable else "anchor_not_better_than_box",
                    "anchor_regions": len(anchor_regions),
                    "box_regions": len(box_regions),
                    "anchor_parsed_count": anchor_parsed_count,
                    "box_parsed_count": box_parsed_count,
                    "anchor_warning_codes": sorted(_warning_codes(anchor_warnings or [])),
                }
        return box_regions, box_warnings, box_trace

    warnings: List[Dict[str, object]] = []
    tokens, page_sizes = _extract_tokens_and_page_sizes(ocr_boxes, image_size)
    anchors, detect_debug = _detect_anchors(tokens, page_sizes, include_debug=True)
    candidates = list((detect_debug or {}).get("candidates") or [])
    median_h_by_page = (detect_debug or {}).get("median_h_by_page") or {}

    candidate_reason: Dict[int, str] = {}
    candidate_entries: List[Dict[str, object]] = []
    for cand in candidates:
        candidate_entries.append(
            {
                "text": cand.text,
                "bbox_px": _anchor_bbox_list(cand),
                "method": cand.label_method,
                "parsed_num": cand.parsed_num,
            }
        )
        candidate_reason[id(cand)] = "not_selected"

    if not anchors:
        raise ValueError("template_anchor_labels_missing")

    deduped_ids = {id(a) for a in anchors}
    for cand in candidates:
        if id(cand) in deduped_ids:
            candidate_reason[id(cand)] = "kept_after_dedupe"
            continue
        h_ref = max(8.0, float(median_h_by_page.get(cand.page_index) or cand.h or 24.0))
        center_thresh_sq = (1.2 * h_ref) * (1.2 * h_ref)
        reason = "deduped_by_overlap_or_proximity"
        for kept in anchors:
            if kept.page_index != cand.page_index:
                continue
            if _rect_iou(cand.rect, kept.rect) >= 0.30 or _distance_sq(cand.center, kept.center) <= center_thresh_sq:
                reason = "deduped_by_overlap_or_proximity"
                break
        candidate_reason[id(cand)] = reason

    anchors.sort(key=lambda a: (a.page_index, a.y, a.x))
    parsed_numbers = [a.parsed_num for a in anchors if a.parsed_num is not None]
    anchor_count = len(anchors)
    unknown_count = sum(1 for a in anchors if a.parsed_num is None)
    out_of_range = sorted(
        {
            int(num)
            for num in parsed_numbers
            if not (1 <= int(num) <= anchor_count)
        }
    )
    freq: Dict[int, int] = {}
    for raw in parsed_numbers:
        num = int(raw)
        if 1 <= num <= anchor_count:
            freq[num] = freq.get(num, 0) + 1
    duplicate_numbers = sorted(num for num, count in freq.items() if count > 1)
    if unknown_count:
        warnings.append(
            {
                "code": "ANCHOR_NUMBER_FALLBACK_ORDER",
                "unknown_count": unknown_count,
                "message": "Some anchors had unreadable numbers; deterministic reading-order numbering applied.",
            }
        )
    if duplicate_numbers:
        warnings.append(
            {
                "code": "ANCHOR_DUPLICATE_NUMBERS",
                "numbers": duplicate_numbers,
                "message": "Duplicate question numbers detected in OCR anchors.",
            }
        )
    if out_of_range:
        warnings.append(
            {
                "code": "ANCHOR_OUT_OF_RANGE_NUMBERS",
                "numbers": out_of_range,
                "message": "Some parsed anchor numbers are outside the detected anchor count.",
            }
        )
    ambiguity_high = (
        unknown_count > max(1, anchor_count // 3)
        or len(duplicate_numbers) >= 2
        or (unknown_count > 0 and bool(duplicate_numbers))
    )
    if ambiguity_high:
        warnings.append(
            {
                "code": "ANCHOR_AMBIGUITY_HIGH",
                "anchor_count": anchor_count,
                "unknown_count": unknown_count,
                "duplicate_numbers": duplicate_numbers,
                "out_of_range_numbers": out_of_range,
                "message": "Anchor numbering ambiguity is high; template should be reviewed.",
            }
        )

    page_tokens: Dict[int, List[_Token]] = {}
    for tok in tokens:
        page_tokens.setdefault(tok.page_index, []).append(tok)

    gate_span_by_anchor: Dict[int, Dict[str, object]] = {}
    eligible_anchors: List[_Anchor] = []
    for anchor in anchors:
        page_w, page_h = page_sizes.get(anchor.page_index) or (float(image_size[0]), float(image_size[1]))
        h_ref = max(8.0, float(median_h_by_page.get(anchor.page_index) or anchor.h or 24.0))
        best_span = _best_geometry_answer_span(
            anchor=anchor,
            page_tokens=page_tokens.get(anchor.page_index) or [],
            page_size=(page_w, page_h),
            h_ref=h_ref,
        )
        if not isinstance(best_span, dict):
            candidate_reason[id(anchor)] = "hard_gate_no_answer_pair"
            continue
        gate_span_by_anchor[id(anchor)] = best_span
        eligible_anchors.append(anchor)

    if not eligible_anchors:
        warnings.append(
            {
                "code": "ANCHOR_HARD_GATE_RELAXED",
                "message": "Answer-pair hard gate rejected all anchors; falling back to ungated anchors.",
            }
        )
        eligible_anchors = list(anchors)

    selected_anchors, selection_warnings, selection_trace = _select_manifest_anchors(
        eligible_anchors,
        tokens,
        page_sizes=page_sizes,
    )
    warnings.extend(selection_warnings)
    if not selected_anchors:
        raise ValueError("template_anchor_selection_empty")

    anchor_index_map = {id(anchor): idx for idx, anchor in enumerate(eligible_anchors)}
    token_map = _assign_tokens_to_anchors(tokens, eligible_anchors)

    implicit_count = sum(1 for a in eligible_anchors if a.label_method == "ocr_anchor_implicit")
    if implicit_count:
        warnings.append(
            {
                "code": "ANCHOR_IMPLICIT_LABELS_USED",
                "count": implicit_count,
                "message": "Some Q labels were inferred from numeric anchors without explicit 'Q'.",
            }
        )

    def _priority(method: str) -> int:
        if method == "ocr_anchor_explicit":
            return 0
        if method == "ocr_anchor_pair":
            return 1
        if method == "ocr_anchor_implicit":
            return 2
        return 3

    processing_order = sorted(
        selected_anchors.items(),
        key=lambda item: (
            int(item[0]),
            _priority(item[1].label_method),
            item[1].page_index,
            item[1].y,
            item[1].x,
        ),
    )
    regions: List[AnchorRegion] = []
    selected_details: List[Dict[str, object]] = []
    reserved_tokens: set[str] = set()
    reserved_answer_boxes: List[Tuple[int, Tuple[float, float, float, float]]] = []
    for rank, (qnum, anchor) in enumerate(processing_order, start=1):
        anchor_idx = anchor_index_map.get(id(anchor))
        if anchor_idx is None:
            continue
        page_w, page_h = page_sizes.get(anchor.page_index) or (float(image_size[0]), float(image_size[1]))
        local_tokens = token_map.get(anchor_idx) or []
        candidate_rects = [anchor.rect] + [tok.rect for tok in local_tokens]
        region_rect = _union_rect(candidate_rects)
        if region_rect:
            region_box = _expand_and_clip(
                region_rect,
                pad_x=max(24.0, page_w * 0.025),
                pad_y=max(20.0, page_h * 0.02),
                page_w=page_w,
                page_h=page_h,
            )
        else:
            ax, ay, aw, ah = anchor.rect
            region_box = _expand_and_clip(
                (
                    max(0.0, ax - 18.0),
                    max(0.0, ay - 28.0),
                    max(120.0, min(page_w * 0.42, 500.0)),
                    max(90.0, min(page_h * 0.22, 320.0)),
                ),
                pad_x=0.0,
                pad_y=0.0,
                page_w=page_w,
                page_h=page_h,
            )

        answer_box, expected_text, used_token_ids = _pick_answer_box(
            anchor=anchor,
            anchor_tokens=local_tokens,
            page_tokens=page_tokens.get(anchor.page_index) or [],
            page_size=(page_w, page_h),
            region_box=region_box,
            reserved_token_keys=reserved_tokens,
            reserved_answer_boxes=reserved_answer_boxes,
            preferred_span=gate_span_by_anchor.get(id(anchor)),
        )
        for token_id in used_token_ids:
            reserved_tokens.add(token_id)
        reserved_answer_boxes.append((anchor.page_index, answer_box))
        selected_details.append(
            {
                "question_id": f"Q{qnum}",
                "text": anchor.text,
                "bbox_px": _anchor_bbox_list(anchor),
                "method": anchor.label_method,
                "parsed_num": anchor.parsed_num,
            }
        )
        regions.append(
            AnchorRegion(
                qid=f"Q{qnum}",
                region=region_box,
                answer_box=answer_box,
                expected_answer_text=expected_text,
                label_method=anchor.label_method,
                index=rank,
                page_index=anchor.page_index,
            )
        )

    overlap_pairs: List[str] = []
    for i in range(len(regions)):
        a = regions[i]
        for j in range(i + 1, len(regions)):
            b = regions[j]
            if a.page_index != b.page_index:
                continue
            overlap = _rect_iou(a.answer_box, b.answer_box)
            if overlap > 0.18:
                overlap_pairs.append(f"{a.qid}-{b.qid}")
    if overlap_pairs:
        warnings.append(
            {
                "code": "ANCHOR_DUPLICATE_ANSWER_BOXES",
                "pairs": overlap_pairs,
                "message": "Detected overlapping answer boxes between questions.",
            }
        )

    decision_map = selection_trace.get("anchor_decisions") if isinstance(selection_trace, dict) else {}
    if not isinstance(decision_map, dict):
        decision_map = {}
    selected_anchor_ids = {id(a) for a in selected_anchors.values()}
    for anchor in anchors:
        if id(anchor) in selected_anchor_ids:
            candidate_reason[id(anchor)] = "selected"
            continue
        decision = str(decision_map.get(id(anchor)) or "").strip().lower()
        if decision:
            candidate_reason[id(anchor)] = decision

    rejected_entries: List[Dict[str, object]] = []
    for cand in candidates:
        reason = str(candidate_reason.get(id(cand)) or "not_selected")
        if reason == "selected":
            continue
        if reason == "kept_after_dedupe":
            reason = "not_selected"
        rejected_entries.append(
            {
                "text": cand.text,
                "bbox_px": _anchor_bbox_list(cand),
                "method": cand.label_method,
                "parsed_num": cand.parsed_num,
                "reason": reason,
            }
        )

    missing_numbers: List[int] = []
    if isinstance(selection_trace, dict):
        raw_missing = selection_trace.get("missing_explicit")
        if isinstance(raw_missing, list):
            missing_numbers = [int(v) for v in raw_missing if isinstance(v, (int, float))]
        raw_expected = selection_trace.get("expected_max_explicit")
        if isinstance(raw_expected, (int, float)) and int(raw_expected) > 0:
            expected_max = int(raw_expected)
            selected_nums = sorted({int(k) for k in selected_anchors.keys()})
            gap_missing = [n for n in range(1, expected_max + 1) if n not in selected_nums]
            missing_numbers = sorted({*missing_numbers, *gap_missing})

    anchor_trace: Dict[str, object] = {
        "candidates": candidate_entries,
        "selected": selected_details,
        "rejected": rejected_entries,
        "missing_numbers": missing_numbers,
        "summary": {
            "candidate_count": len(candidate_entries),
            "selected_count": len(selected_details),
            "rejected_count": len(rejected_entries),
            "missing_count": len(missing_numbers),
        },
    }

    regions.sort(key=lambda r: int(str(r.qid).lstrip("Q") or "0"))
    return regions, warnings, anchor_trace
