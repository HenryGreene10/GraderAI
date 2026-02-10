from __future__ import annotations

from dataclasses import dataclass
import math
import re
from typing import Dict, List, Optional, Tuple

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


def _detect_anchors(tokens: List[_Token], page_sizes: Dict[int, Tuple[float, float]]) -> List[_Anchor]:
    if not tokens:
        return []
    median_h_by_page = _median_word_height_by_page(tokens)
    candidates: List[_Anchor] = []
    for tok in tokens:
        page_w = (page_sizes.get(tok.page_index) or (0.0, 0.0))[0]
        cand = _candidate_from_token(tok, page_w)
        if cand:
            candidates.append(cand)
    candidates.extend(_pair_q_tokens_with_number_tokens(tokens, median_h_by_page))
    if not candidates:
        return []
    return _dedupe_anchor_candidates(candidates, median_h_by_page)


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
    cleaned = re.sub(r"[(){}\[\],;:]+", "", text.strip())
    compact = re.sub(r"\s+", " ", cleaned).strip()
    if not compact:
        return 0
    if _ANSWER_REM_RE.match(compact):
        return 120
    if _ANSWER_NUMERIC_RE.match(compact):
        return 90
    if _ANSWER_ANY_DIGIT_RE.search(compact):
        return 45
    return 5


def _pick_answer_box(
    *,
    anchor: _Anchor,
    anchor_tokens: List[_Token],
    page_tokens: List[_Token],
    page_size: Tuple[float, float],
    region_box: Tuple[float, float, float, float],
) -> Tuple[Tuple[float, float, float, float], str]:
    page_w, page_h = page_size
    ax, ay, aw, ah = anchor.rect
    anchor_cx, anchor_cy = anchor.center
    search_x0 = max(0.0, ax - max(30.0, page_w * 0.04))
    search_x1 = min(page_w, ax + max(240.0, page_w * 0.65))
    search_y0 = max(0.0, ay - max(70.0, page_h * 0.10))
    search_y1 = min(page_h, ay + max(180.0, page_h * 0.24))

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

    candidates: List[Tuple[float, Tuple[float, float, float, float], str]] = []

    for tok in nearby:
        base = _score_answer_text(tok.text)
        if base <= 0:
            continue
        cx, cy = tok.center
        dist = math.sqrt(_distance_sq((cx, cy), (anchor_cx, anchor_cy)))
        score = float(base) - (dist / max(1.0, max(page_w, page_h))) * 28.0
        if cx > ax + aw * 0.3:
            score += 10.0
        candidates.append((score, tok.rect, tok.text))

    by_line: Dict[int, List[_Token]] = {}
    for tok in nearby:
        by_line.setdefault(tok.line_index, []).append(tok)
    for line_idx in sorted(by_line.keys()):
        line_tokens = sorted(by_line[line_idx], key=lambda t: (t.x, t.word_index))
        for i in range(len(line_tokens) - 1):
            t1 = line_tokens[i]
            t2 = line_tokens[i + 1]
            merged = f"{t1.text} {t2.text}".strip()
            base = _score_answer_text(merged)
            if base <= 0:
                continue
            rect = _union_rect([t1.rect, t2.rect])
            if not rect:
                continue
            cx = rect[0] + rect[2] / 2.0
            cy = rect[1] + rect[3] / 2.0
            dist = math.sqrt(_distance_sq((cx, cy), (anchor_cx, anchor_cy)))
            score = float(base + 8) - (dist / max(1.0, max(page_w, page_h))) * 28.0
            if cx > ax + aw * 0.3:
                score += 12.0
            candidates.append((score, rect, merged))

    if candidates:
        candidates.sort(key=lambda item: (-item[0], item[1][1], item[1][0]))
        _score, rect, text = candidates[0]
        clipped = _expand_and_clip(rect, pad_x=10.0, pad_y=8.0, page_w=page_w, page_h=page_h)
        return clipped, normalize_answer_text(text)

    fallback_pool = [t for t in anchor_tokens if not _overlaps_label(t, anchor)]
    if fallback_pool:
        fallback_pool.sort(key=lambda t: (_distance_sq(t.center, anchor.center), t.y, t.x))
        tok = fallback_pool[0]
        rect = _expand_and_clip(tok.rect, pad_x=10.0, pad_y=8.0, page_w=page_w, page_h=page_h)
        return rect, normalize_answer_text(tok.text)

    rx, ry, rw, rh = region_box
    fallback = (
        min(page_w - 1.0, max(0.0, ax + aw + 20.0)),
        min(page_h - 1.0, max(0.0, ay + max(6.0, ah * 0.2))),
        max(24.0, min(rw * 0.22, page_w * 0.2)),
        max(18.0, min(rh * 0.22, page_h * 0.12)),
    )
    return _expand_and_clip(fallback, pad_x=0.0, pad_y=0.0, page_w=page_w, page_h=page_h), ""


def build_anchor_template_regions(
    *,
    ocr_boxes: object,
    image_size: Tuple[int, int],
) -> Tuple[List[AnchorRegion], List[Dict[str, object]]]:
    warnings: List[Dict[str, object]] = []
    tokens, page_sizes = _extract_tokens_and_page_sizes(ocr_boxes, image_size)
    anchors = _detect_anchors(tokens, page_sizes)
    if not anchors:
        raise ValueError("template_anchor_labels_missing")

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

    anchor_numbers = _assign_anchor_numbers(anchors)
    token_map = _assign_tokens_to_anchors(tokens, anchors)
    page_tokens: Dict[int, List[_Token]] = {}
    for tok in tokens:
        page_tokens.setdefault(tok.page_index, []).append(tok)

    implicit_count = sum(1 for a in anchors if a.label_method == "ocr_anchor_implicit")
    if implicit_count:
        warnings.append(
            {
                "code": "ANCHOR_IMPLICIT_LABELS_USED",
                "count": implicit_count,
                "message": "Some Q labels were inferred from numeric anchors without explicit 'Q'.",
            }
        )

    ordered = sorted(range(len(anchors)), key=lambda idx: anchor_numbers[idx])
    regions: List[AnchorRegion] = []
    for rank, anchor_idx in enumerate(ordered, start=1):
        anchor = anchors[anchor_idx]
        qnum = anchor_numbers[anchor_idx]
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

        answer_box, expected_text = _pick_answer_box(
            anchor=anchor,
            anchor_tokens=local_tokens,
            page_tokens=page_tokens.get(anchor.page_index) or [],
            page_size=(page_w, page_h),
            region_box=region_box,
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

    regions.sort(key=lambda r: int(str(r.qid).lstrip("Q") or "0"))
    return regions, warnings
