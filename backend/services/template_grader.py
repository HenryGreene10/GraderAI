from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
import logging
import re
from typing import Any, Dict, List, Tuple

from PIL import Image

from ..models.schemas import CriterionScore, GradeResult, Overlay, OverlayMark, QuestionGrade
from .coords import px_to_pdf
from .scanner import MIN_DIM_PX, MAX_DIM_PX, PDF_DPI
from .template_align import AlignmentResult, align_student_to_template

logger = logging.getLogger(__name__)

TEMPLATE_RUBRIC_VERSION = "template-1"
TEMPLATE_PROMPT_VERSION = "template-1"


@dataclass
class TemplateGradeOutput:
    grade_result: GradeResult
    overlay: Overlay
    needs_review: bool
    alignment: AlignmentResult
    student_answers: List[Dict[str, Any]]


class TemplateAlignmentError(Exception):
    pass


def _normalize_text(value: str) -> str:
    raw = " ".join((value or "").split())
    if not raw:
        return ""
    raw = re.sub(r"(?i)\b(r|rem|remainder)\s*([0-9]+)\b", r"R\2", raw)
    raw = re.sub(r"(?i)(\d)\s*R\s*([0-9]+)", r"\1 R\2", raw)
    return raw.lower()


def _extract_number(value: str) -> float | None:
    match = re.search(r"-?\d+(?:\.\d+)?", value or "")
    if not match:
        return None
    try:
        return float(match.group(0))
    except Exception:
        return None


def _score_answer(expected: str, got: str) -> Tuple[float, str, bool]:
    expected_norm = _normalize_text(expected)
    got_norm = _normalize_text(got)
    if not expected_norm:
        return 0.0, "Missing expected answer", True
    exp_num = _extract_number(expected_norm)
    got_num = _extract_number(got_norm)
    if exp_num is not None and got_num is not None:
        tol = max(0.01, abs(exp_num) * 0.02)
        if abs(exp_num - got_num) <= tol:
            return 1.0, f"Numeric match within {tol:.2f}", False
        return 0.0, f"Expected {exp_num} got {got_num}", False
    if expected_norm == got_norm:
        return 1.0, "Exact match", False
    expected_tokens = set(expected_norm.split())
    got_tokens = set(got_norm.split())
    if expected_tokens and got_tokens:
        overlap = len(expected_tokens & got_tokens)
        if overlap >= max(1, len(expected_tokens) // 2):
            return 1.0, f"Keyword overlap {overlap}", False
    return 0.0, "Answer mismatch", False


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
    needs_review = False

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
        score, rationale, low_conf = _score_answer(expected, student_text)
        if low_conf or not student_text:
            needs_review = True

        items.append(
            QuestionGrade(
                question_id=qid or str(len(items) + 1),
                qtype="short_answer",
                score=score,
                max_score=1.0,
                criteria=[
                    CriterionScore(
                        name="template",
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
            }
        )

    total = sum(item.score for item in items)
    result = GradeResult(
        submission_id="",
        total_score=total,
        total_max=float(len(items)),
        items=items,
        rubric_version=TEMPLATE_RUBRIC_VERSION,
        prompt_version=TEMPLATE_PROMPT_VERSION,
        needs_review=needs_review,
    )

    overlay = _build_template_overlay(template_regions, result, aligned_img.size)
    return TemplateGradeOutput(
        grade_result=result,
        overlay=overlay,
        needs_review=needs_review,
        alignment=alignment,
        student_answers=answers,
    )


def _build_template_overlay(
    template_regions: List[Dict[str, Any]],
    grade_result: GradeResult,
    template_size: Tuple[int, int],
) -> Overlay:
    norm_w, norm_h = float(template_size[0]), float(template_size[1])
    page_w_pt = norm_w / PDF_DPI * 72.0
    page_h_pt = norm_h / PDF_DPI * 72.0
    marks: List[OverlayMark] = []

    score_w = 140.0
    score_h = 26.0
    score_margin = 24.0
    score_x = page_w_pt - score_w - score_margin
    score_y = page_h_pt - score_h - score_margin
    marks.append(
        OverlayMark(
            tool="bubble",
            coords=[score_x, score_y, score_w, score_h],
            text=f"Score: {grade_result.total_score:.0f}/{grade_result.total_max:.0f}",
        )
    )

    for item in grade_result.items:
        region = next((r for r in template_regions if str(r.get("qid")) == item.question_id), None)
        if not region:
            continue
        answer_box = region.get("answer_box") or {}
        if not answer_box:
            continue
        x0 = float(answer_box.get("x") or 0.0)
        y0 = float(answer_box.get("y") or 0.0)
        w = float(answer_box.get("w") or 0.0)
        h = float(answer_box.get("h") or 0.0)
        anchor_x_px = x0 + w - 18.0
        anchor_y_px = y0 + 6.0
        x_pt, y_pt = px_to_pdf(anchor_x_px, anchor_y_px, (norm_w, norm_h), (page_w_pt, page_h_pt))
        tool = "check" if item.score >= item.max_score * 0.5 else "cross"
        marks.append(OverlayMark(tool=tool, coords=[x_pt, y_pt], text=None))

    return Overlay(page=1, marks=marks)

