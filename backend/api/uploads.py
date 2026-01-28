import datetime as dt
import os
from typing import Optional, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from ..auth import get_current_user_id
from ..config import GRADED_BUCKET, OVERLAYS_BUCKET, SUBMISSIONS_BUCKET
from ..services.db import get_upload, update_upload
from ..services.llm_grader import grade_with_llm
from ..services.marking import build_overlay_from_answers
from ..services.report import get_page_sizes, render_debug_layout_pdf, render_marked_pdf
from ..services.storage import download_submission_bytes, strip_bucket_prefix, upload_bytes, upload_json
from ..services.supabase_client import get_supabase
from ..services.debug_artifacts import (
    debug_enabled,
    draw_marks_overlay,
    log_debug,
    upload_debug_artifact,
)

router = APIRouter(prefix="/api/uploads", tags=["uploads"])


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
            "id,owner_id,storage_path,ocr_status,ocr_text,ocr_boxes,mime_type,"
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
        grade_result, answers = await grade_with_llm(ocr_text)
        grade_result.submission_id = row["id"]

        storage_path = row.get("storage_path")
        if not storage_path:
            raise HTTPException(status_code=400, detail="Missing storage_path")

        pdf_source_path = row.get("normalized_pdf_path") or storage_path
        pdf_source_bytes = download_submission_bytes(pdf_source_path)
        pdf_mime = "application/pdf" if row.get("normalized_pdf_path") else row.get("mime_type")
        page_sizes = get_page_sizes(pdf_source_bytes, pdf_mime)
        page_width_pt, page_height_pt = page_sizes[0]
        page_width_in = page_width_pt / 72.0
        page_height_in = page_height_pt / 72.0
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

        overlay, needs_review_from_overlay, unplaced_items, debug_layout = build_overlay_from_answers(
            answers,
            ocr_boxes,
            page_sizes,
            normalized_size=normalized_size,
            total_score=grade_result.total_score,
            total_max=grade_result.total_max,
        )

        pdf_bytes = render_marked_pdf(pdf_source_bytes, pdf_mime, overlay)

        owner_id = row.get("owner_id") or user_id or "unknown"
        pdf_key = f"{owner_id}/{row['id']}.pdf"
        overlay_key = f"{owner_id}/{row['id']}.json"

        upload_bytes(GRADED_BUCKET, pdf_key, pdf_bytes, "application/pdf")
        try:
            upload_json(OVERLAYS_BUCKET, overlay_key, overlay.model_dump())
        except Exception:
            pass

        needs_review = (
            grade_result.needs_review
            or needs_review_from_overlay
            or bool(row.get("needs_review"))
        )
        grade_result.unplaced_items = unplaced_items

        debug_layout_path = None
        if os.getenv("DEBUG_LAYOUT") == "1":
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

        if debug_enabled(debug):
            owner_id = row.get("owner_id") or user_id or "unknown"
            try:
                normalized_image_path = row.get("normalized_image_path")
                if normalized_image_path:
                    normalized_bytes = download_submission_bytes(normalized_image_path)
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
                upload_debug_artifact(
                    owner_id,
                    row["id"],
                    "marked.pdf",
                    pdf_bytes,
                    "application/pdf",
                )
            except Exception:
                logger.exception("Failed to create marks debug bundle for %s", row["id"])

        update_upload(
            row["id"],
            {
                "status": "pdf_ready",
                "needs_review": needs_review,
                "graded_pdf_path": pdf_key,
                "overlay_path": overlay_key,
                "overlay_json": overlay.model_dump(),
                "grade_json": {
                    **grade_result.model_dump(),
                    "answers": [a.to_dict() for a in answers],
                    "debug_layout_path": debug_layout_path,
                },
                "rubric_version": grade_result.rubric_version,
                "prompt_version": grade_result.prompt_version,
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
