import datetime as dt
import logging
import os
from io import BytesIO
from typing import Optional, Literal, Tuple

from PIL import Image

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from ..auth import get_current_user_id
from ..config import GRADED_BUCKET, OVERLAYS_BUCKET, SUBMISSIONS_BUCKET
from ..services.db import get_assignment, get_upload, update_upload
from ..services import ocr as ocr_service
from ..services.ocr import normalize_ocr_result
from ..services.llm_grader import grade_with_llm
from ..services.answer_extraction import extract_answers_from_ocr
from ..services.scoring import score_answer_maps
from ..services.template_regions import (
    build_overlay_from_regions,
    expected_answers_from_regions,
    extract_answers_from_regions,
    parse_regions_payload,
)
from ..services.marking import build_overlay_from_answers
from ..models.schemas import Overlay, GradeResult
from ..services.report import (
    MISSING_OVERLAY_BANNER,
    build_minimal_overlay,
    get_page_sizes,
    render_debug_layout_pdf,
    render_marked_pdf,
)
from ..services.scanner import image_bytes_to_pdf
from ..services.template_grader import TemplateAlignmentError, grade_with_template
from ..services.storage import (
    download_submission_bytes,
    normalize_storage_path,
    strip_bucket_prefix,
    upload_bytes,
    upload_json,
)
from ..services.supabase_client import get_supabase
from ..services.debug_artifacts import (
    debug_enabled,
    draw_marks_overlay,
    draw_rects_overlay,
    draw_template_overlay,
    log_debug,
    upload_debug_artifact,
)

router = APIRouter(prefix="/api/uploads", tags=["uploads"])
logger = logging.getLogger(__name__)


def _extract_signed_url(result: object) -> Optional[str]:
    if isinstance(result, dict):
        return (
            result.get("signedURL")
            or result.get("signedUrl")
            or result.get("signed_url")
            or result.get("url")
        )
    getter = getattr(result, "get", None)
    if callable(getter):
        return (
            getter("signedURL")
            or getter("signedUrl")
            or getter("signed_url")
            or getter("url")
        )
    return None


@router.get("/{upload_id}/preview")
def preview_upload(
    upload_id: str,
    user_id: str = Depends(get_current_user_id),
):
    row = get_upload(upload_id, user_id, columns="id,owner_id,storage_path,normalized_pdf_path")
    storage_path = row.get("normalized_pdf_path") or row.get("storage_path")
    if not storage_path:
        raise HTTPException(status_code=404, detail="Missing storage_path")

    sb = get_supabase()
    if sb is None:
        raise HTTPException(status_code=503, detail="Supabase client unavailable")

    rel = strip_bucket_prefix(storage_path, SUBMISSIONS_BUCKET)
    try:
        result = sb.storage.from_(SUBMISSIONS_BUCKET).create_signed_url(rel, 300)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"signed_url_failed: {exc}")

    url = _extract_signed_url(result)
    if not url:
        raise HTTPException(status_code=500, detail="signed_url_missing")

    return {"url": url}


@router.get("/{upload_id}/ocr")
def get_upload_ocr(
    upload_id: str,
    user_id: str = Depends(get_current_user_id),
    include_boxes: bool = Query(False),
):
    row = get_upload(
        upload_id,
        user_id,
        columns="id,owner_id,ocr_status,ocr_error,ocr_text,ocr_confidence,ocr_boxes",
    )
    text = (row.get("ocr_text") or "").strip()
    status = (row.get("ocr_status") or "").strip().lower()
    payload = {
        "ocr_status": status,
        "ocr_error": row.get("ocr_error"),
        "ocr_confidence": row.get("ocr_confidence"),
        "text": text,
        "text_len": len(text) if text else 0,
    }
    if include_boxes:
        payload["ocr_boxes"] = row.get("ocr_boxes")
    return payload


@router.get("/{upload_id}/student-answers")
async def get_student_answers(
    upload_id: str,
    user_id: str = Depends(get_current_user_id),
    include_metadata: bool = Query(False),
):
    row = get_upload(
        upload_id,
        user_id,
        columns="id,owner_id,ocr_status,ocr_error,ocr_text,ocr_boxes,ocr_confidence",
    )
    status = (row.get("ocr_status") or "").strip().lower()
    if status != "done":
        raise HTTPException(status_code=409, detail="ocr_not_done")
    ocr_text = str(row.get("ocr_text") or "").strip()
    if not ocr_text:
        raise HTTPException(status_code=400, detail="ocr_text_missing")

    answers, prompt_version = await extract_answers_from_ocr(ocr_text, role="student")
    response = {
        "answers": answers,
        "prompt_version": prompt_version,
    }
    if include_metadata:
        response["metadata"] = {
            "ocr_text": ocr_text,
            "ocr_boxes": row.get("ocr_boxes"),
            "ocr_confidence": row.get("ocr_confidence"),
        }
    return response


@router.delete("/{upload_id}")
def delete_upload(
    upload_id: str,
    user_id: str = Depends(get_current_user_id),
):
    row = get_upload(upload_id, user_id, columns="id,owner_id,storage_path")
    storage_path = row.get("storage_path")
    if not storage_path:
        raise HTTPException(status_code=404, detail="Missing storage_path")

    sb = get_supabase()
    if sb is None:
        raise HTTPException(status_code=503, detail="Supabase client unavailable")

    try:
        rel = strip_bucket_prefix(storage_path, SUBMISSIONS_BUCKET)
        sb.storage.from_(SUBMISSIONS_BUCKET).remove([rel])
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"storage_delete_failed: {exc}")

    try:
        sb.table("uploads").delete().eq("id", row["id"]).execute()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"db_delete_failed: {exc}")

    return {"ok": True, "upload_id": row["id"]}


def _utc_iso() -> str:
    return dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


class OverridePayload(BaseModel):
    overall_status: Literal["correct", "partial", "incorrect", "reviewed"]
    note: Optional[str] = None


def _ensure_overlay(
    upload_id: str,
    result: GradeResult,
    overlay: Optional[Overlay],
) -> Tuple[Overlay, bool]:
    if overlay and overlay.marks:
        return overlay, False
    fallback = build_minimal_overlay(upload_id, result.total_score, result.total_max)
    return fallback, True


async def run_grade_pipeline(
    upload_id: str,
    user_id: str,
    *,
    force: bool = False,
    debug: bool = False,
) -> dict:
    row = get_upload(
        upload_id,
        user_id,
        columns=(
            "id,owner_id,assignment_id,storage_path,ocr_status,ocr_text,ocr_boxes,mime_type,"
            "graded_pdf_path,status,normalized_pdf_path,normalized_width_px,"
            "normalized_height_px,needs_review,normalized_image_path"
        ),
    )
    status = (row.get("status") or "").strip().lower()
    if row.get("graded_pdf_path") and not force:
        return {"ok": True, "upload_id": row["id"], "graded_pdf_path": row.get("graded_pdf_path"), "already": True}
    if status in {"grading", "pdf_ready"} and not force:
        return {"ok": True, "upload_id": row["id"], "graded_pdf_path": row.get("graded_pdf_path"), "already": True}

    if (row.get("ocr_status") or "").strip().lower() != "done":
        raise HTTPException(status_code=409, detail="OCR not complete")

    ocr_text = (row.get("ocr_text") or "").strip()
    if not ocr_text:
        raise HTTPException(status_code=400, detail="Missing ocr_text")
    ocr_boxes = row.get("ocr_boxes")
    if not ocr_boxes:
        raise HTTPException(status_code=400, detail="Missing ocr_boxes")

    update_upload(
        row["id"],
        {"status": "grading", "ocr_error": None, "updated_at": _utc_iso()},
    )

    try:
        storage_path = row.get("storage_path")
        if not storage_path:
            raise HTTPException(status_code=400, detail="Missing storage_path")

        assignment = None
        template_regions = None
        template_storage_path = None
        template_version = None
        template_png = None
        if row.get("assignment_id"):
            try:
                assignment = get_assignment(
                    row["assignment_id"],
                    user_id,
                    columns="id,template_storage_path,template_regions_json,template_version",
                )
            except HTTPException:
                assignment = None
        if assignment:
            template_regions = assignment.get("template_regions_json") or []
            template_storage_path = assignment.get("template_storage_path")
            template_version = assignment.get("template_version")

        regions_map, _ = parse_regions_payload(template_regions)
        regions_present = isinstance(template_regions, dict) and bool(regions_map)
        template_available = bool(template_regions and template_storage_path)
        template_used = False
        template_alignment_used = False
        template_alignment = None
        debug_image_bytes = None
        template_ocr_rects = []
        debug_layout = None
        needs_review_from_overlay = False
        unplaced_items = []
        marks_placed = None
        marks_skipped_missing = None
        marks_skipped_needs_review = None
        answers = []

        answers_json = None
        answer_rows = []
        answer_prompt_version = None

        if regions_present:
            expected_qids = [f"Q{i}" for i in range(1, 10)]
            ignored_extra = len([qid for qid in regions_map.keys() if qid not in expected_qids])
            logger.info(
                "regions_present upload_id=%s region_count=%s",
                row["id"],
                len(regions_map),
            )
            student_answers, missing_qids = extract_answers_from_regions(
                ocr_boxes,
                template_regions,
                fallback_size=(
                    float(row.get("normalized_width_px") or 0.0),
                    float(row.get("normalized_height_px") or 0.0),
                ),
            )
            key_answers: dict[str, str] = {}
            filtered_students: dict[str, str] = {}
            for qid in expected_qids:
                entry = regions_map.get(qid) or {}
                key_answers[qid] = str(entry.get("expected_answer_text") or "").strip()
                filtered_students[qid] = str(student_answers.get(qid) or "").strip()
            missing = sorted(
                {qid for qid in expected_qids if qid not in regions_map or qid in missing_qids}
            )
            logger.info(
                "grading_qids=%s expected_qids=%s ignored_extra=%s missing=%s",
                expected_qids,
                expected_qids,
                ignored_extra,
                missing,
            )
            grade_result, answers, answer_rows = score_answer_maps(key_answers, filtered_students)
            grade_result.submission_id = row["id"]
            answers_json = {"key": key_answers, "student": filtered_students}
            if missing:
                needs_review_from_overlay = True
            pdf_source_path = row.get("normalized_pdf_path") or storage_path
            pdf_source_key = strip_bucket_prefix(pdf_source_path, SUBMISSIONS_BUCKET)
            sb = get_supabase()
            if sb is None:
                raise RuntimeError("Supabase client unavailable")
            pdf_source_bytes = sb.storage.from_(SUBMISSIONS_BUCKET).download(pdf_source_key)
            if not pdf_source_bytes:
                raise RuntimeError(f"Submission not found: {pdf_source_path}")
            pdf_mime = "application/pdf" if row.get("normalized_pdf_path") else row.get("mime_type")
            page_sizes = get_page_sizes(pdf_source_bytes, pdf_mime)
            page_size = page_sizes[0] if page_sizes else (612.0, 792.0)
            normalized_size = (
                float(row.get("normalized_width_px") or 0.0),
                float(row.get("normalized_height_px") or 0.0),
            )
            overlay, marks_placed, marks_skipped_missing, marks_skipped_needs_review, unplaced_items = build_overlay_from_regions(
                grade_result,
                template_regions,
                normalized_size,
                page_size,
            )
            if unplaced_items:
                needs_review_from_overlay = True
            template_used = True
            if marks_placed is not None:
                logger.info(
                    "marks_placed=%s skipped_missing_region=%s skipped_needs_review=%s",
                    marks_placed,
                    marks_skipped_missing,
                    marks_skipped_needs_review,
                )
        elif template_available and row.get("normalized_image_path"):
            template_png = download_submission_bytes(template_storage_path)
            student_png = download_submission_bytes(row.get("normalized_image_path"))
            try:
                template_output = await grade_with_template(
                    student_png,
                    template_png,
                    template_regions,
                    ocr_service.extract_text,
                )
            except TemplateAlignmentError as exc:
                owner_id = row.get("owner_id") or user_id or "unknown"
                overlay_key = f"{owner_id}/{row['id']}.json"
                overlay_path = normalize_storage_path(OVERLAYS_BUCKET, overlay_key)
                overlay_payload = build_minimal_overlay(row["id"], 0.0, 0.0).model_dump()
                try:
                    upload_json(OVERLAYS_BUCKET, overlay_key, overlay_payload)
                except Exception as upload_exc:
                    logger.warning(
                        "overlay_upload_failed upload_id=%s error=%s",
                        row["id"],
                        upload_exc,
                    )
                update_upload(
                    row["id"],
                    {
                        "status": "error",
                        "needs_review": True,
                        "ocr_error": f"template_alignment_failed: {exc}",
                        "grade_json": {
                            "template_used": False,
                            "template_error": f"Template alignment failed: {exc}",
                        },
                        "overlay_path": overlay_path,
                        "overlay_json": overlay_payload,
                        "updated_at": _utc_iso(),
                    },
                )
                raise HTTPException(status_code=422, detail=f"Template alignment failed: {exc}")
            template_used = True
            grade_result = template_output.grade_result
            grade_result.submission_id = row["id"]
            overlay = template_output.overlay
            needs_review_from_overlay = template_output.needs_review
            answers = template_output.student_answers
            answer_rows = template_output.answer_rows
            template_alignment = template_output.alignment
            debug_image_bytes = template_output.alignment.aligned_png
            template_ocr_rects = template_output.ocr_rects
            unplaced_items = template_output.unplaced_items
            marks_placed = template_output.marks_placed
            marks_skipped_missing = template_output.marks_skipped_missing
            template_alignment_used = True
            if isinstance(template_output.student_answers, list):
                key_map = {}
                student_map = {}
                for row_item in template_output.student_answers:
                    qid = str(row_item.get("question_id") or "").strip()
                    if not qid:
                        continue
                    key_map[qid] = str(row_item.get("expected_answer") or "").strip()
                    student_map[qid] = str(row_item.get("student_answer") or "").strip()
                answers_json = {"key": key_map, "student": student_map}

            pdf_source_bytes = image_bytes_to_pdf(template_output.alignment.aligned_png)
            pdf_mime = "application/pdf"
            page_sizes = get_page_sizes(pdf_source_bytes, pdf_mime)
        else:
            if template_available:
                logger.warning("Template exists but normalized image missing for %s; falling back", row["id"])
            if template_storage_path:
                template_bytes = download_submission_bytes(template_storage_path)
                raw = await ocr_service.extract_text(image_bytes=template_bytes)
                norm = normalize_ocr_result(raw)
                key_text = str(norm.get("text") or "").strip()
                if not key_text:
                    raise HTTPException(status_code=400, detail="template_ocr_empty")
                key_answers, answer_prompt_version = await extract_answers_from_ocr(
                    key_text,
                    role="answer_key",
                )
                student_answers, _ = await extract_answers_from_ocr(
                    ocr_text,
                    role="student",
                )
                grade_result, answers, answer_rows = score_answer_maps(key_answers, student_answers)
                grade_result.submission_id = row["id"]
                answers_json = {"key": key_answers, "student": student_answers}
            else:
                grade_result, answers = await grade_with_llm(ocr_text)
                grade_result.submission_id = row["id"]
                answers_json = None
                answer_rows = []

            pdf_source_path = row.get("normalized_pdf_path") or storage_path
            pdf_source_key = strip_bucket_prefix(pdf_source_path, SUBMISSIONS_BUCKET)
            sb = get_supabase()
            if sb is None:
                raise RuntimeError("Supabase client unavailable")
            pdf_source_bytes = sb.storage.from_(SUBMISSIONS_BUCKET).download(pdf_source_key)
            if not pdf_source_bytes:
                raise RuntimeError(f"Submission not found: {pdf_source_path}")
            pdf_mime = "application/pdf" if row.get("normalized_pdf_path") else row.get("mime_type")
            page_sizes = get_page_sizes(pdf_source_bytes, pdf_mime)

            normalized_size = (
                float(row.get("normalized_width_px") or 0.0),
                float(row.get("normalized_height_px") or 0.0),
            )
            overlay, needs_review_from_overlay, unplaced_items, debug_layout = build_overlay_from_answers(
                answers,
                ocr_boxes,
                page_sizes,
                normalized_size=normalized_size,
                total_score=grade_result.total_score,
                total_max=grade_result.total_max,
            )

        if answer_rows:
            counts = {"correct": 0, "incorrect": 0, "needs_review": 0}
            for row_item in answer_rows:
                status = str(row_item.get("status") or "")
                if status in counts:
                    counts[status] += 1
            logger.info(
                "grading_counts upload_id=%s total=%s correct=%s incorrect=%s needs_review=%s",
                row["id"],
                sum(counts.values()),
                counts["correct"],
                counts["incorrect"],
                counts["needs_review"],
            )
        if template_used and marks_placed is not None and marks_skipped_missing is not None:
            logger.info(
                "mark_placement upload_id=%s placed=%s skipped_missing=%s skipped_needs_review=%s",
                row["id"],
                marks_placed,
                marks_skipped_missing,
                marks_skipped_needs_review,
            )

        overlay, overlay_missing = _ensure_overlay(row["id"], grade_result, overlay)
        if overlay_missing:
            logger.warning("overlay_missing upload_id=%s using minimal overlay", row["id"])
            needs_review_from_overlay = True

        page_width_pt, page_height_pt = page_sizes[0]
        page_width_in = page_width_pt / 72.0
        page_height_in = page_height_pt / 72.0
        if template_alignment_used:
            normalized_size = (
                float(page_width_pt / 72.0 * 300.0),
                float(page_height_pt / 72.0 * 300.0),
            )
            if debug_image_bytes:
                try:
                    with Image.open(BytesIO(debug_image_bytes)) as img:
                        normalized_size = (float(img.width), float(img.height))
                except Exception:
                    pass
        else:
            normalized_size = (
                float(row.get("normalized_width_px") or 0.0),
                float(row.get("normalized_height_px") or 0.0),
            )
        if debug_enabled(debug):
            norm_w, norm_h = normalized_size
            sx = page_width_pt / norm_w if norm_w else None
            sy = page_height_pt / norm_h if norm_h else None
            log_debug(
                "pdf_map",
                {
                    "page_w_pt": round(page_width_pt, 2),
                    "page_h_pt": round(page_height_pt, 2),
                    "page_w_in": round(page_width_in, 3),
                    "page_h_in": round(page_height_in, 3),
                    "dpi": 300,
                    "sx": sx,
                    "sy": sy,
                    "y_flip": True,
                },
            )

        smoke_score_text = None
        if needs_review_from_overlay:
            if grade_result.total_max > 0:
                smoke_score_text = f"Score: {grade_result.total_score:.0f}/{grade_result.total_max:.0f}"
            else:
                smoke_score_text = "Score: (unavailable) — NEEDS REVIEW"

        normalized_size_px = None
        if not template_used and normalized_size[0] > 0 and normalized_size[1] > 0:
            normalized_size_px = normalized_size

        pdf_bytes = render_marked_pdf(
            pdf_source_bytes,
            pdf_mime,
            overlay,
            missing_overlay_text=MISSING_OVERLAY_BANNER,
            smoke_score_text=smoke_score_text,
            normalized_size_px=normalized_size_px,
        )

        owner_id = row.get("owner_id") or user_id or "unknown"
        pdf_key = f"{owner_id}/{row['id']}.pdf"
        overlay_key = f"{owner_id}/{row['id']}.json"
        overlay_path = normalize_storage_path(OVERLAYS_BUCKET, overlay_key)
        overlay_payload = overlay.model_dump()

        logger.info(
            "overlay_generated upload_id=%s overlay_path=%s marks=%s",
            row["id"],
            overlay_path,
            len(overlay.marks),
        )

        upload_bytes(GRADED_BUCKET, pdf_key, pdf_bytes, "application/pdf")
        try:
            upload_json(OVERLAYS_BUCKET, overlay_key, overlay_payload)
        except Exception as exc:
            logger.warning("overlay_upload_failed upload_id=%s error=%s", row["id"], exc)
            needs_review_from_overlay = True

        debug_layout_path = None
        if os.getenv("DEBUG_LAYOUT") == "1" and debug_layout:
            try:
                debug_pdf = render_debug_layout_pdf(
                    pdf_source_bytes,
                    pdf_mime,
                    ocr_boxes,
                    debug_layout,
                    normalized_size,
                )
                debug_layout_path = f"{owner_id}/{row['id']}-layout.pdf"
                upload_bytes(GRADED_BUCKET, debug_layout_path, debug_pdf, "application/pdf")
            except Exception:
                debug_layout_path = None

        needs_review = (
            grade_result.needs_review
            or needs_review_from_overlay
            or bool(row.get("needs_review"))
        )
        grade_result.unplaced_items = unplaced_items

        if debug_enabled(debug):
            owner_id = row.get("owner_id") or user_id or "unknown"
            try:
                normalized_bytes = debug_image_bytes
                if not normalized_bytes:
                    normalized_image_path = row.get("normalized_image_path")
                    if normalized_image_path:
                        normalized_bytes = download_submission_bytes(normalized_image_path)
                if normalized_bytes:
                    marks_png, mark_info = draw_marks_overlay(
                        normalized_bytes,
                        overlay,
                        normalized_size,
                        (page_width_pt, page_height_pt),
                    )
                    upload_debug_artifact(
                        owner_id,
                        row["id"],
                        "marks_overlay.png",
                        marks_png,
                        "image/png",
                    )
                    log_debug("marks", {"count": mark_info.get("marks"), "bboxes": mark_info.get("mark_bboxes")})
                if template_used and template_png:
                    template_overlay, _ = draw_template_overlay(template_png, template_regions or [])
                    upload_debug_artifact(
                        owner_id,
                        row["id"],
                        "template_overlay.png",
                        template_overlay,
                        "image/png",
                    )
                if template_used and normalized_bytes and template_ocr_rects:
                    ocr_overlay = draw_rects_overlay(normalized_bytes, template_ocr_rects)
                    upload_debug_artifact(
                        owner_id,
                        row["id"],
                        "ocr_overlay.png",
                        ocr_overlay,
                        "image/png",
                    )
                upload_debug_artifact(
                    owner_id,
                    row["id"],
                    "marked.pdf",
                    pdf_bytes,
                    "application/pdf",
                )
            except Exception:
                logger.exception("Failed to create marks debug bundle for %s", row["id"])

        answers_payload = []
        if answers:
            if hasattr(answers[0], "to_dict"):
                answers_payload = [a.to_dict() for a in answers]
            else:
                answers_payload = answers

        grade_json = {
            **grade_result.model_dump(),
            "answers": answers_payload,
            "debug_layout_path": debug_layout_path,
        }
        if answers_json:
            grade_json["answers_json"] = answers_json
        if answer_rows:
            grade_json["answer_rows"] = answer_rows
        if answer_prompt_version:
            grade_json["answer_prompt_version"] = answer_prompt_version
        if template_used:
            grade_json["template_used"] = True
            grade_json["template_version_used"] = template_version
            if template_alignment:
                grade_json["alignment"] = {
                    "ok": template_alignment.ok,
                    "match_count": template_alignment.match_count,
                    "inliers": template_alignment.inliers,
                    "error": template_alignment.error,
                }

        update_upload(
            row["id"],
            {
                "status": "pdf_ready",
                "needs_review": needs_review,
                "graded_pdf_path": pdf_key,
                "overlay_path": overlay_path,
                "overlay_json": overlay_payload,
                "grade_json": grade_json,
                "rubric_version": grade_result.rubric_version,
                "prompt_version": grade_result.prompt_version,
                "template_version_used": template_version if template_used else None,
                "updated_at": _utc_iso(),
            },
        )
        return {
            "ok": True,
            "upload_id": row["id"],
            "needs_review": needs_review,
            "graded_pdf_path": pdf_key,
        }
    except HTTPException:
        raise
    except Exception as exc:
        update_upload(
            row["id"],
            {
                "status": "error",
                "ocr_error": f"grade_failed: {exc}",
                "updated_at": _utc_iso(),
            },
        )
        raise HTTPException(status_code=500, detail=f"grade_failed: {exc}")


@router.post("/{upload_id}/grade")
async def grade_upload(
    upload_id: str,
    user_id: str = Depends(get_current_user_id),
):
    return await run_grade_pipeline(upload_id, user_id)


@router.post("/{upload_id}/retry")
async def retry_upload(
    upload_id: str,
    user_id: str = Depends(get_current_user_id),
    debug: bool = Query(False),
):
    row = get_upload(
        upload_id,
        user_id,
        columns="id,owner_id,ocr_status,graded_pdf_path",
    )
    if row.get("graded_pdf_path"):
        return {"ok": True, "upload_id": row["id"], "graded_pdf_path": row.get("graded_pdf_path"), "already": True}
    if (row.get("ocr_status") or "").strip().lower() != "done":
        from .ocr import run_ocr_for_upload

        return await run_ocr_for_upload(row["id"], user_id, debug=debug)
    return await run_grade_pipeline(row["id"], user_id, force=True, debug=debug)


@router.post("/{upload_id}/override")
def override_upload(
    upload_id: str,
    body: OverridePayload,
    user_id: str = Depends(get_current_user_id),
):
    row = get_upload(upload_id, user_id, columns="id,owner_id")

    sb = get_supabase()
    if sb is None:
        raise HTTPException(status_code=503, detail="Supabase client unavailable")

    overrides_json = {"overall_status": body.overall_status}
    note = (body.note or "").strip()
    if note:
        overrides_json["note"] = note

    try:
        sb.table("overrides").insert(
            {
                "upload_id": row["id"],
                "owner_id": row.get("owner_id") or user_id,
                "overrides_json": overrides_json,
                "created_at": _utc_iso(),
                "updated_at": _utc_iso(),
            }
        ).execute()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"override_insert_failed: {exc}")

    status = "reviewed" if body.overall_status == "reviewed" else "overridden"
    update_upload(
        row["id"],
        {
            "status": status,
            "updated_at": _utc_iso(),
        },
    )

    return {"ok": True, "upload_id": row["id"], "status": status}
