import datetime as dt
import logging
import os
from io import BytesIO
import re
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
from ..services.scoring import canonicalize_quotient_remainder, score_answer_maps
from ..services.template_regions import (
    build_overlay_from_regions,
    extract_answers_from_regions,
    parse_regions_payload,
)
from ..services.template_manifest import load_template_manifest, manifest_to_template_regions_payload
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
from ..services.template_align import align_student_to_template
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


def _regions_have_px_boxes(regions_payload: object) -> bool:
    regions_map, _ = parse_regions_payload(regions_payload)
    for entry in regions_map.values():
        bbox = entry.get("bbox_px")
        if isinstance(bbox, (list, tuple)) and len(bbox) >= 4:
            vals = [bbox[0], bbox[1], bbox[2], bbox[3]]
        else:
            box = entry.get("answer_box") or {}
            vals = [box.get("x"), box.get("y"), box.get("w"), box.get("h")]
        try:
            max_val = max(float(v) for v in vals if v is not None)
        except ValueError:
            continue
        if max_val > 1.5:
            return True
    return False


def _qid_sort_key(qid: str) -> tuple[int, str]:
    digits = "".join(ch for ch in str(qid) if ch.isdigit())
    if digits:
        return int(digits), str(qid)
    return 10_000, str(qid)


def _filter_to_expected_qids(expected_qids: list[str], values: dict[str, str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for qid in expected_qids:
        out[qid] = str(values.get(qid) or "").strip()
    return out


def _template_mark_integrity_reasons(
    *,
    expected_qids: list[str],
    graded_qids: list[str],
    marks_placed: int | None,
    marks_skipped_missing: int | None,
    unplaced_items: list[str],
) -> list[str]:
    reasons: list[str] = []
    expected = [str(qid) for qid in expected_qids]
    graded = [str(qid) for qid in graded_qids]
    expected_set = set(expected)
    graded_set = set(graded)
    duplicate_qids: list[str] = []
    seen: set[str] = set()
    for qid in graded:
        if qid in seen and qid not in duplicate_qids:
            duplicate_qids.append(qid)
        seen.add(qid)
    unknown_qids = sorted((graded_set - expected_set), key=_qid_sort_key)
    missing_qids = sorted((expected_set - graded_set), key=_qid_sort_key)

    question_marks_count = int(marks_placed or 0) + int(marks_skipped_missing or 0)
    if question_marks_count != len(graded):
        reasons.append(f"graded_mark_count_mismatch:{question_marks_count}!={len(graded)}")
    if len(graded) != len(expected):
        reasons.append(f"manifest_item_count_mismatch:{len(graded)}!={len(expected)}")
    if question_marks_count != len(expected):
        reasons.append(f"manifest_mark_count_mismatch:{question_marks_count}!={len(expected)}")
    missing_count = int(marks_skipped_missing or 0)
    if missing_count > 0:
        reasons.append(f"manifest_missing_marks:{missing_count}")
    if unknown_qids:
        reasons.append(f"manifest_unknown_qids:{','.join(unknown_qids)}")
    if missing_qids:
        reasons.append(f"manifest_missing_qids:{','.join(missing_qids)}")
    if duplicate_qids:
        reasons.append(f"manifest_duplicate_qids:{','.join(duplicate_qids)}")
    if unplaced_items:
        reasons.append(f"manifest_unplaced_items:{len(unplaced_items)}")
    return reasons


def _template_size_px(
    template_width_px: object,
    template_height_px: object,
    regions_meta: dict,
) -> Optional[Tuple[float, float]]:
    try:
        w = float(template_width_px or 0.0)
        h = float(template_height_px or 0.0)
        if w > 0 and h > 0:
            return (w, h)
    except Exception:
        pass
    try:
        w = float(regions_meta.get("template_width_px") or 0.0)
        h = float(regions_meta.get("template_height_px") or 0.0)
        if w > 0 and h > 0:
            return (w, h)
    except Exception:
        pass
    return None


def _scale_bbox(bbox: object, sx: float, sy: float) -> object:
    if not isinstance(bbox, list):
        return bbox
    scaled: list[float] = []
    for i, val in enumerate(bbox):
        try:
            v = float(val)
        except Exception:
            scaled.append(val)
            continue
        scaled.append(v * (sx if i % 2 == 0 else sy))
    return scaled


def _scale_ocr_boxes_to_size(
    ocr_boxes: object,
    target_size: Tuple[float, float],
) -> tuple[object, bool]:
    if not isinstance(ocr_boxes, dict):
        return ocr_boxes, False
    analyze = ocr_boxes.get("analyzeResult")
    if not isinstance(analyze, dict):
        return ocr_boxes, False
    read_results = analyze.get("readResults")
    if not isinstance(read_results, list):
        return ocr_boxes, False

    target_w, target_h = target_size
    scaled_any = False
    out: dict = {
        **ocr_boxes,
        "analyzeResult": {
            **analyze,
            "readResults": [],
        },
    }
    out_read_results = out["analyzeResult"]["readResults"]
    for page in read_results:
        if not isinstance(page, dict):
            out_read_results.append(page)
            continue
        try:
            src_w = float(page.get("width") or 0.0)
            src_h = float(page.get("height") or 0.0)
        except Exception:
            src_w = 0.0
            src_h = 0.0
        if src_w <= 0 or src_h <= 0:
            out_read_results.append(page)
            continue
        sx = target_w / src_w
        sy = target_h / src_h
        scaled_page = {**page, "width": target_w, "height": target_h}
        scaled_lines = []
        for line in page.get("lines") or []:
            if not isinstance(line, dict):
                scaled_lines.append(line)
                continue
            scaled_line = {**line}
            scaled_line["boundingBox"] = _scale_bbox(line.get("boundingBox"), sx, sy)
            scaled_words = []
            for word in line.get("words") or []:
                if not isinstance(word, dict):
                    scaled_words.append(word)
                    continue
                scaled_word = {**word}
                scaled_word["boundingBox"] = _scale_bbox(word.get("boundingBox"), sx, sy)
                scaled_words.append(scaled_word)
            scaled_line["words"] = scaled_words
            scaled_lines.append(scaled_line)
        scaled_page["lines"] = scaled_lines
        out_read_results.append(scaled_page)
        scaled_any = True
    return out, scaled_any


def _scale_png_to_size(png_bytes: bytes, target_size: Tuple[float, float]) -> bytes:
    tw = max(1, int(round(float(target_size[0]))))
    th = max(1, int(round(float(target_size[1]))))
    with Image.open(BytesIO(png_bytes)) as img:
        resized = img.convert("RGB").resize((tw, th), Image.BICUBIC)
    out = BytesIO()
    resized.save(out, format="PNG")
    return out.getvalue()


def _entry_bbox_px(entry: dict, size: Tuple[float, float]) -> Optional[Tuple[float, float, float, float]]:
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
    if max(x, y, w, h) <= 1.5:
        sx, sy = size
        return x * sx, y * sy, (x + w) * sx, (y + h) * sy
    return x, y, x + w, y + h


def _save_qid_crops_debug(
    owner_id: str,
    upload_id: str,
    frame_png: bytes,
    regions_map: dict[str, dict],
    qids: list[str],
) -> None:
    with Image.open(BytesIO(frame_png)) as img:
        frame = img.convert("RGB")
        fw, fh = float(frame.width), float(frame.height)
        for qid in qids:
            entry = regions_map.get(qid) or {}
            rect = _entry_bbox_px(entry, (fw, fh))
            if not rect:
                continue
            x0, y0, x1, y1 = rect
            x0 = max(0, min(frame.width, int(round(x0))))
            y0 = max(0, min(frame.height, int(round(y0))))
            x1 = max(0, min(frame.width, int(round(x1))))
            y1 = max(0, min(frame.height, int(round(y1))))
            if x1 <= x0 or y1 <= y0:
                continue
            crop = frame.crop((x0, y0, x1, y1))
            buf = BytesIO()
            crop.save(buf, format="PNG")
            safe_qid = re.sub(r"[^A-Za-z0-9_-]+", "_", str(qid))
            upload_debug_artifact(
                owner_id,
                upload_id,
                f"qid_{safe_qid}_crop.png",
                buf.getvalue(),
                "image/png",
            )


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
        columns=(
            "id,owner_id,assignment_id,ocr_status,ocr_error,ocr_text,ocr_boxes,ocr_confidence,"
            "normalized_width_px,normalized_height_px"
        ),
    )
    status = (row.get("ocr_status") or "").strip().lower()
    if status != "done":
        raise HTTPException(status_code=409, detail="ocr_not_done")
    ocr_text = str(row.get("ocr_text") or "").strip()
    if not ocr_text:
        raise HTTPException(status_code=400, detail="ocr_text_missing")

    assignment_id = str(row.get("assignment_id") or "").strip()
    if assignment_id:
        try:
            assignment = get_assignment(
                assignment_id,
                user_id,
                columns="id,owner_id,template_regions_json,template_version",
            )
        except HTTPException:
            assignment = None
        if assignment:
            regions_payload = assignment.get("template_regions_json") or {}
            regions_map, _ = parse_regions_payload(regions_payload)
            if regions_map:
                expected_qids = sorted(list(regions_map.keys()), key=_qid_sort_key)
                fallback_size: Optional[Tuple[float, float]] = None
                try:
                    nw = float(row.get("normalized_width_px") or 0.0)
                    nh = float(row.get("normalized_height_px") or 0.0)
                    if nw > 0 and nh > 0:
                        fallback_size = (nw, nh)
                except Exception:
                    fallback_size = None
                region_answers, missing_qids = extract_answers_from_regions(
                    row.get("ocr_boxes"),
                    regions_payload,
                    fallback_size=fallback_size,
                )
                answers = _filter_to_expected_qids(expected_qids, region_answers)
                response = {
                    "answers": answers,
                    "prompt_version": "template-regions-v1",
                }
                if include_metadata:
                    response["metadata"] = {
                        "source": "template_regions",
                        "assignment_id": assignment_id,
                        "template_version": assignment.get("template_version"),
                        "expected_qids": expected_qids,
                        "missing_qids": [qid for qid in expected_qids if qid in set(missing_qids)],
                        "ocr_text": ocr_text,
                        "ocr_boxes": row.get("ocr_boxes"),
                        "ocr_confidence": row.get("ocr_confidence"),
                    }
                return response

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
    source: str = "uploads.grade_pipeline",
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
        assignment_lookup_error = None
        template_regions = None
        template_storage_path = None
        template_version = None
        template_png = None
        template_width_px = None
        template_height_px = None
        template_manifest = None
        template_manifest_embedded = False
        template_degraded_reasons: list[str] = []
        if row.get("assignment_id"):
            try:
                assignment = get_assignment(
                    row["assignment_id"],
                    user_id,
                    columns=(
                        "id,owner_id,template_storage_path,template_regions_json,template_version,"
                        "template_width_px,template_height_px"
                    ),
                )
            except HTTPException as exc:
                assignment_lookup_error = str(exc.detail or "assignment_lookup_failed")
                assignment = None
        if row.get("assignment_id") and assignment is None:
            reason = assignment_lookup_error or "assignment_lookup_failed"
            update_upload(
                row["id"],
                {
                    "status": "error",
                    "needs_review": True,
                    "ocr_error": f"assignment_lookup_failed: {reason}",
                    "updated_at": _utc_iso(),
                },
            )
            raise HTTPException(status_code=409, detail=f"assignment_lookup_failed: {reason}")
        if assignment:
            template_regions = assignment.get("template_regions_json") or []
            template_storage_path = assignment.get("template_storage_path")
            template_version = assignment.get("template_version")
            template_width_px = assignment.get("template_width_px")
            template_height_px = assignment.get("template_height_px")
            if template_storage_path:
                try:
                    template_manifest, template_manifest_embedded = load_template_manifest(
                        template_regions,
                        template_version=int(template_version or 1),
                        template_width_px=template_width_px,
                        template_height_px=template_height_px,
                        require_approved=True,
                    )
                    template_regions = manifest_to_template_regions_payload(template_manifest)
                except Exception as exc:
                    update_upload(
                        row["id"],
                        {
                            "status": "error",
                            "needs_review": True,
                            "ocr_error": f"template_manifest_unapproved: {exc}",
                            "updated_at": _utc_iso(),
                        },
                    )
                    raise HTTPException(status_code=409, detail=f"template_manifest_unapproved: {exc}")

        regions_map, regions_meta = parse_regions_payload(template_regions)
        regions_present = bool(regions_map)
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
        region_frame_source = None
        region_frame_error = None

        answers_json = None
        answer_rows = []
        answer_prompt_version = None
        effective_normalized_size = (
            float(row.get("normalized_width_px") or 0.0),
            float(row.get("normalized_height_px") or 0.0),
        )

        if assignment:
            logger.info(
                "grading_with_template assignment_id=%s has_regions=%s qids=%s",
                row.get("assignment_id"),
                regions_present,
                list(regions_map.keys()),
            )
            if not regions_present:
                update_upload(
                    row["id"],
                    {
                        "status": "error",
                        "needs_review": True,
                        "ocr_error": "template_regions_missing",
                        "updated_at": _utc_iso(),
                    },
                )
                raise HTTPException(status_code=409, detail="template_regions_missing")

        if regions_present:
            template_used = True
            if template_manifest:
                expected_qids = [q.question_id for q in template_manifest.questions]
            else:
                expected_qids = sorted((str(qid) for qid in regions_map.keys()), key=_qid_sort_key)
            allow_unaligned_fallback = os.getenv("ALLOW_UNALIGNED_REGION_FALLBACK") == "1"
            template_size = _template_size_px(template_width_px, template_height_px, regions_meta)

            student_png = None
            if row.get("normalized_image_path"):
                try:
                    student_png = download_submission_bytes(row.get("normalized_image_path"))
                except Exception as exc:
                    region_frame_error = f"student_image_load_failed: {exc}"

            if template_storage_path:
                try:
                    template_png = download_submission_bytes(template_storage_path)
                except Exception as exc:
                    region_frame_error = f"template_image_load_failed: {exc}"
            else:
                region_frame_error = "template_storage_path_missing"

            frame_ocr_boxes = None
            frame_png_bytes = None

            if template_png and student_png:
                template_alignment = align_student_to_template(student_png, template_png)
                if template_alignment.ok:
                    frame_png_bytes = template_alignment.aligned_png
                    debug_image_bytes = frame_png_bytes
                    raw = await ocr_service.extract_text(image_bytes=frame_png_bytes)
                    norm = normalize_ocr_result(raw)
                    frame_ocr_boxes = norm.get("boxes") or {}
                    region_frame_source = "aligned_image_ocr"
                    template_alignment_used = True
                    template_ocr_rects = []
                else:
                    region_frame_error = template_alignment.error or "alignment_failed"
                    needs_review_from_overlay = True

            if frame_ocr_boxes is None and student_png and template_size:
                try:
                    frame_png_bytes = _scale_png_to_size(student_png, template_size)
                    debug_image_bytes = frame_png_bytes
                    raw = await ocr_service.extract_text(image_bytes=frame_png_bytes)
                    norm = normalize_ocr_result(raw)
                    frame_ocr_boxes = norm.get("boxes") or {}
                    region_frame_source = "scaled_image_ocr"
                    needs_review_from_overlay = True
                except Exception as exc:
                    region_frame_error = f"scaled_image_ocr_failed: {exc}"

            if frame_ocr_boxes is None and template_size:
                scaled_boxes, scaled_ok = _scale_ocr_boxes_to_size(ocr_boxes, template_size)
                if scaled_ok:
                    frame_ocr_boxes = scaled_boxes
                    region_frame_source = "scaled_ocr_boxes"
                    needs_review_from_overlay = True

            if frame_ocr_boxes is None:
                if allow_unaligned_fallback:
                    frame_ocr_boxes = ocr_boxes
                    region_frame_source = "explicit_unaligned_fallback"
                    needs_review_from_overlay = True
                    logger.warning(
                        "template_regions_unaligned_fallback upload_id=%s enabled_by_env=1",
                        row["id"],
                    )
                else:
                    raise HTTPException(
                        status_code=422,
                        detail=(
                            "template_frame_unavailable: aligned/scaled frame required; "
                            "set ALLOW_UNALIGNED_REGION_FALLBACK=1 to force fallback"
                        ),
                    )

            student_answers, missing_qids = extract_answers_from_regions(
                frame_ocr_boxes,
                template_regions,
                fallback_size=template_size,
            )
            key_answers: dict[str, str]
            if template_manifest:
                key_answers = {
                    q.question_id: canonicalize_quotient_remainder(str(q.expected_answer_text or "").strip())
                    for q in template_manifest.questions
                }
            else:
                key_answers = {}
                for qid in expected_qids:
                    entry = regions_map.get(qid) or {}
                    key_answers[qid] = canonicalize_quotient_remainder(str(entry.get("expected_answer_text") or "").strip())
            filtered_students = _filter_to_expected_qids(expected_qids, student_answers)
            missing = sorted({qid for qid in expected_qids if qid in missing_qids}, key=_qid_sort_key)
            grade_result, answers, answer_rows = score_answer_maps(key_answers, filtered_students)
            grade_result.submission_id = row["id"]
            answers_json = {"key": key_answers, "student": filtered_students}
            if missing:
                needs_review_from_overlay = True
                template_degraded_reasons.append("missing_region_answers")

            if debug_enabled(debug) and frame_png_bytes:
                owner_id = row.get("owner_id") or user_id or "unknown"
                try:
                    _save_qid_crops_debug(
                        owner_id,
                        row["id"],
                        frame_png_bytes,
                        regions_map,
                        expected_qids,
                    )
                except Exception:
                    logger.exception("Failed to save qid crop debug artifacts for %s", row["id"])

            if frame_png_bytes is not None:
                pdf_source_bytes = image_bytes_to_pdf(frame_png_bytes)
                pdf_mime = "application/pdf"
            else:
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
            normalized_size = effective_normalized_size
            if frame_png_bytes is not None:
                try:
                    with Image.open(BytesIO(frame_png_bytes)) as img:
                        normalized_size = (float(img.width), float(img.height))
                except Exception:
                    if template_size:
                        normalized_size = (float(template_size[0]), float(template_size[1]))
            elif template_size:
                normalized_size = (float(template_size[0]), float(template_size[1]))
            effective_normalized_size = normalized_size
            overlay, marks_placed, marks_skipped_missing, marks_skipped_needs_review, unplaced_items = build_overlay_from_regions(
                grade_result,
                template_regions,
                normalized_size,
                page_size,
                page_sizes_pt=page_sizes,
            )
            if template_manifest:
                graded_qids = [str(item.question_id) for item in grade_result.items]
                integrity_reasons = _template_mark_integrity_reasons(
                    expected_qids=expected_qids,
                    graded_qids=graded_qids,
                    marks_placed=marks_placed,
                    marks_skipped_missing=marks_skipped_missing,
                    unplaced_items=unplaced_items,
                )
                if integrity_reasons:
                    needs_review_from_overlay = True
                    template_degraded_reasons.extend(integrity_reasons)
            elif unplaced_items:
                needs_review_from_overlay = True
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
            effective_normalized_size = normalized_size
            overlay, needs_review_from_overlay, unplaced_items, debug_layout = build_overlay_from_answers(
                answers,
                ocr_boxes,
                page_sizes,
                normalized_size=normalized_size,
                total_score=grade_result.total_score,
                total_max=grade_result.total_max,
            )

        overlay, overlay_missing = _ensure_overlay(row["id"], grade_result, overlay)
        if overlay_missing:
            logger.warning("overlay_missing upload_id=%s using minimal overlay", row["id"])
            needs_review_from_overlay = True

        page_width_pt, page_height_pt = page_sizes[0]
        page_width_in = page_width_pt / 72.0
        page_height_in = page_height_pt / 72.0
        normalized_size = effective_normalized_size
        if normalized_size[0] <= 0 or normalized_size[1] <= 0:
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
        if template_used and normalized_size[0] > 0 and normalized_size[1] > 0:
            normalized_size_px = normalized_size
        elif (
            template_regions
            and _regions_have_px_boxes(template_regions)
            and template_width_px
            and template_height_px
        ):
            normalized_size_px = (float(template_width_px), float(template_height_px))
        elif not template_used and normalized_size[0] > 0 and normalized_size[1] > 0:
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
            "pipeline": {
                "source": source,
                "template_used": bool(template_used),
            },
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
            if template_manifest:
                grade_json["template_manifest_schema"] = template_manifest.schema_version
                grade_json["template_manifest_question_count"] = template_manifest.question_count
                grade_json["template_manifest_embedded"] = bool(template_manifest_embedded)
            if template_degraded_reasons:
                grade_json["template_degraded"] = True
                grade_json["template_degraded_reasons"] = sorted(set(template_degraded_reasons))
            if region_frame_source:
                grade_json["template_region_frame"] = region_frame_source
            if region_frame_error:
                grade_json["template_region_frame_error"] = region_frame_error
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
            "pipeline_source": source,
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


async def run_unified_submission_pipeline(
    upload_id: str,
    user_id: str,
    *,
    force: bool = False,
    debug: bool = False,
    source: str = "uploads.unified",
) -> dict:
    ran_ocr_stage = False
    row = get_upload(
        upload_id,
        user_id,
        columns="id,owner_id,status,ocr_status,ocr_error,graded_pdf_path,needs_review,grade_json,overlay_path",
    )
    ocr_status = str(row.get("ocr_status") or "").strip().lower()
    if ocr_status != "done":
        from .ocr import run_ocr_for_upload

        await run_ocr_for_upload(row["id"], user_id, debug=debug, pipeline_source=f"{source}:ocr")
        ran_ocr_stage = True
        row = get_upload(
            upload_id,
            user_id,
            columns="id,status,ocr_status,ocr_error,graded_pdf_path,needs_review,grade_json,overlay_path",
        )
        if str(row.get("status") or "").strip().lower() == "error" and not row.get("graded_pdf_path"):
            detail = row.get("ocr_error") or "pipeline_failed"
            raise HTTPException(status_code=500, detail=str(detail))
        if row.get("graded_pdf_path") and (ran_ocr_stage or not force):
            return {
                "ok": True,
                "upload_id": row["id"],
                "needs_review": bool(row.get("needs_review")),
                "graded_pdf_path": row.get("graded_pdf_path"),
                "already": True,
                "pipeline_source": source,
            }

    return await run_grade_pipeline(
        upload_id,
        user_id,
        force=force,
        debug=debug,
        source=f"{source}:grade",
    )


@router.post("/{upload_id}/grade")
async def grade_upload(
    upload_id: str,
    user_id: str = Depends(get_current_user_id),
):
    return await run_unified_submission_pipeline(upload_id, user_id, source="uploads.grade")


@router.post("/{upload_id}/retry")
async def retry_upload(
    upload_id: str,
    user_id: str = Depends(get_current_user_id),
    debug: bool = Query(False),
):
    return await run_unified_submission_pipeline(
        upload_id,
        user_id,
        force=True,
        debug=debug,
        source="uploads.retry",
    )


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
