from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
import logging
from typing import Any, Dict, List, Tuple

from PIL import Image

from ..models.schemas import CriterionScore, GradeResult, Overlay, OverlayMark, QuestionGrade
from .coords import px_to_pdf
from .scanner import MIN_DIM_PX, MAX_DIM_PX, PDF_DPI
from .template_align import AlignmentResult, align_student_to_template
from .scoring import PROMPT_VERSION, RUBRIC_VERSION, score_quotient_remainder

logger = logging.getLogger(__name__)


@dataclass
class TemplateGradeOutput:
    grade_result: GradeResult
    overlay: Overlay
    needs_review: bool
    alignment: AlignmentResult
    student_answers: List[Dict[str, Any]]
    ocr_rects: List[Tuple[float, float, float, float]]
    answer_rows: List[Dict[str, Any]]
    marks_placed: int
    marks_skipped_missing: int
    unplaced_items: List[str]


class TemplateAlignmentError(Exception):
    pass


def _prepare_crop(image: Image.Image) -> Image.Image:
    width, height = image.size
    max_dim = max(width, height)
    if max_dim > MAX_DIM_PX:
        scale = MAX_DIM_PX / float(max_dim)
        width = max(1, int(round(width * scale)))
        height = max(1, int(round(height * scale)))
        image = image.resize((width, height), Image.BICUBIC)
    if min(width, height) < MIN_DIM_PX:
        new_w = max(width, MIN_DIM_PX)
        new_h = max(height, MIN_DIM_PX)
        canvas = Image.new("RGB", (new_w, new_h), color=(255, 255, 255))
        offset = ((new_w - width) // 2, (new_h - height) // 2)
        canvas.paste(image, offset)
        return canvas
    return image


def _crop_to_png(image: Image.Image, box: Tuple[float, float, float, float]) -> bytes:
    crop = image.crop(box)
    crop = _prepare_crop(crop)
    buf = BytesIO()
    crop.save(buf, format="PNG")
    return buf.getvalue()


async def grade_with_template(
    student_png: bytes,
    template_png: bytes,
    template_regions: List[Dict[str, Any]],
    ocr_func,
) -> TemplateGradeOutput:
    alignment = align_student_to_template(student_png, template_png)
    if not alignment.ok:
        raise TemplateAlignmentError(alignment.error or "alignment_failed")
    aligned_img = Image.open(BytesIO(alignment.aligned_png)).convert("RGB")

    items: List[QuestionGrade] = []
    answers: List[Dict[str, Any]] = []
    answer_rows: List[Dict[str, Any]] = []
    needs_review = False
    ocr_rects: List[Tuple[float, float, float, float]] = []

    for region in template_regions:
        qid = str(region.get("qid") or "")
        answer_box = region.get("answer_box") or {}
        if not answer_box:
            needs_review = True
            continue
        x0 = float(answer_box.get("x") or 0.0)
        y0 = float(answer_box.get("y") or 0.0)
        w = float(answer_box.get("w") or 0.0)
        h = float(answer_box.get("h") or 0.0)
        x1 = x0 + w
        y1 = y0 + h
        expected = str(region.get("expected_answer_text") or "").strip()
        crop_png = _crop_to_png(aligned_img, (x0, y0, x1, y1))
        raw = await ocr_func(image_bytes=crop_png)
        student_text = str((raw or {}).get("text") or "").strip()
        ocr_rects.extend(_offset_rects(_extract_rects(raw), x0, y0))
        status, score, rationale, low_conf = score_quotient_remainder(expected, student_text)
        if low_conf:
            needs_review = True

        items.append(
            QuestionGrade(
                question_id=qid or str(len(items) + 1),
                qtype="short_answer",
                score=score,
                max_score=1.0,
                criteria=[
                    CriterionScore(
                        name="quotient-remainder",
                        score=score,
                        max_score=1.0,
                        rationale=rationale,
                    )
                ],
                rationale=rationale,
                low_confidence=low_conf,
            )
        )
        answers.append(
            {
                "question_id": qid,
                "student_answer": student_text,
                "expected_answer": expected,
                "status": status,
                "score": score,
            }
        )
        answer_rows.append(
            {
                "question_id": qid,
                "status": status,
                "score": score,
                "expected_raw": expected,
                "observed_raw": student_text,
            }
        )

    total = sum(item.score for item in items)
    result = GradeResult(
        submission_id="",
        total_score=total,
        total_max=float(len(items)),
        items=items,
        rubric_version=RUBRIC_VERSION,
        prompt_version=PROMPT_VERSION,
        needs_review=needs_review,
    )

    overlay, marks_placed, marks_skipped_missing, unplaced = _build_template_overlay(
        template_regions,
        result,
        aligned_img.size,
    )
    if unplaced:
        needs_review = True
        result.needs_review = True
        result.unplaced_items = unplaced
    return TemplateGradeOutput(
        grade_result=result,
        overlay=overlay,
        needs_review=needs_review,
        alignment=alignment,
        student_answers=answers,
        ocr_rects=ocr_rects,
        answer_rows=answer_rows,
        marks_placed=marks_placed,
        marks_skipped_missing=marks_skipped_missing,
        unplaced_items=unplaced,
    )


def _build_template_overlay(
    template_regions: List[Dict[str, Any]],
    grade_result: GradeResult,
    template_size: Tuple[int, int],
) -> Tuple[Overlay, int, int, List[str]]:
    norm_w, norm_h = float(template_size[0]), float(template_size[1])
    page_w_pt = norm_w / PDF_DPI * 72.0
    page_h_pt = norm_h / PDF_DPI * 72.0
    marks: List[OverlayMark] = []
    unplaced: List[str] = []
    placed = 0
    skipped_missing = 0
    marks_by_page: Dict[int, List[OverlayMark]] = {}
    fallback_count_by_page: Dict[int, int] = {}

    def _fallback_mark(item_id: str, item_symbol: str, page_no: int) -> None:
        idx = fallback_count_by_page.get(page_no, 0)
        y = max(24.0, page_h_pt - 56.0 - idx * 18.0)
        mark = OverlayMark(tool="note", coords=[24.0, y], text=f"{item_id}: {item_symbol} (fallback)")
        fallback_count_by_page[page_no] = idx + 1
        if page_no == 1:
            marks.append(mark)
        marks_by_page.setdefault(page_no, []).append(mark)

    score_w = 140.0
    score_h = 26.0
    score_margin = 24.0
    score_x = page_w_pt - score_w - score_margin
    score_y = page_h_pt - score_h - score_margin
    score_mark = OverlayMark(
        tool="bubble",
        coords=[score_x, score_y, score_w, score_h],
        text=f"Score: {grade_result.total_score:.0f}/{grade_result.total_max:.0f}",
    )
    marks.append(score_mark)
    marks_by_page.setdefault(1, []).append(score_mark)

    for item in grade_result.items:
        region = next((r for r in template_regions if str(r.get("qid")) == item.question_id), None)
        symbol = "REVIEW" if item.low_confidence else ("✓" if item.score >= item.max_score else "✗")
        if not region:
            unplaced.append(item.question_id)
            skipped_missing += 1
            _fallback_mark(item.question_id, symbol, 1)
            continue
        page_index = int(region.get("page_index") or 0)
        page_no = page_index + 1
        answer_box = region.get("answer_box") or {}
        if not answer_box:
            unplaced.append(item.question_id)
            skipped_missing += 1
            _fallback_mark(item.question_id, symbol, page_no)
            continue
        x0 = float(answer_box.get("x") or 0.0)
        y0 = float(answer_box.get("y") or 0.0)
        w = float(answer_box.get("w") or 0.0)
        h = float(answer_box.get("h") or 0.0)
        anchor_x_px = x0 + w - 18.0
        anchor_y_px = y0 + 6.0
        x_pt, y_pt = px_to_pdf(anchor_x_px, anchor_y_px, (norm_w, norm_h), (page_w_pt, page_h_pt))
        if item.low_confidence:
            mark = OverlayMark(tool="note", coords=[x_pt, y_pt], text="REVIEW")
        else:
            tool = "check" if item.score >= item.max_score else "cross"
            mark = OverlayMark(tool=tool, coords=[x_pt, y_pt], text=None)
        if page_no == 1:
            marks.append(mark)
        marks_by_page.setdefault(page_no, []).append(mark)
        placed += 1

    meta: Dict[str, Any] = {"coords_space": "pt"}
    if any(page_no != 1 for page_no in marks_by_page.keys()):
        meta["marks_by_page"] = {
            str(page_no): [m.model_dump() for m in page_marks]
            for page_no, page_marks in marks_by_page.items()
        }
    return Overlay(page=1, marks=marks, meta=meta), placed, skipped_missing, unplaced


def _extract_rects(raw: Any) -> List[Tuple[float, float, float, float]]:
    if not isinstance(raw, dict):
        return []
    analyze = (raw or {}).get("analyzeResult", {})
    read_results = analyze.get("readResults") or []
    rects: List[Tuple[float, float, float, float]] = []
    for page in read_results:
        for line in page.get("lines") or []:
            bbox = line.get("boundingBox") or []
            if len(bbox) < 8:
                continue
            xs = bbox[0::2]
            ys = bbox[1::2]
            rects.append((min(xs), min(ys), max(xs), max(ys)))
    return rects


def _offset_rects(rects: List[Tuple[float, float, float, float]], dx: float, dy: float) -> List[Tuple[float, float, float, float]]:
    return [(x0 + dx, y0 + dy, x1 + dx, y1 + dy) for x0, y0, x1, y1 in rects]
