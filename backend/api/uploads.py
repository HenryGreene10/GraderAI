import datetime as dt
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from ..auth import get_current_user_id
from ..config import GRADED_BUCKET, OVERLAYS_BUCKET, SUBMISSIONS_BUCKET
from ..services.db import get_upload, update_upload
from ..services.llm_grader import grade_with_llm
from ..services.marking import build_overlay_from_answers
from ..services.report import get_page_sizes, render_marked_pdf
from ..services.storage import download_submission_bytes, strip_bucket_prefix, upload_bytes, upload_json
from ..services.supabase_client import get_supabase

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
    row = get_upload(upload_id, user_id, columns="id,owner_id,storage_path")
    storage_path = row.get("storage_path")
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


@router.post("/{upload_id}/grade")
async def grade_upload(
    upload_id: str,
    user_id: str = Depends(get_current_user_id),
):
    row = get_upload(
        upload_id,
        user_id,
        columns="id,owner_id,storage_path,ocr_status,ocr_text,ocr_boxes,mime_type",
    )
    if (row.get("ocr_status") or "").strip().lower() != "done":
        raise HTTPException(status_code=409, detail="OCR not complete")

    ocr_text = (row.get("ocr_text") or "").strip()
    if not ocr_text:
        raise HTTPException(status_code=400, detail="Missing ocr_text")
    ocr_boxes = row.get("ocr_boxes")
    if not ocr_boxes:
        raise HTTPException(status_code=400, detail="Missing ocr_boxes")

    grade_result, answers = await grade_with_llm(ocr_text)
    grade_result.submission_id = row["id"]

    storage_path = row.get("storage_path")
    if not storage_path:
        raise HTTPException(status_code=400, detail="Missing storage_path")
    original_bytes = download_submission_bytes(storage_path)
    page_sizes = get_page_sizes(original_bytes, row.get("mime_type"))

    overlay, needs_review_from_overlay = build_overlay_from_answers(
        answers,
        ocr_boxes,
        page_sizes,
    )

    pdf_bytes = render_marked_pdf(original_bytes, row.get("mime_type"), overlay)

    owner_id = row.get("owner_id") or user_id or "unknown"
    pdf_key = f"{owner_id}/{row['id']}.pdf"
    overlay_key = f"{owner_id}/{row['id']}.json"

    try:
        upload_bytes(GRADED_BUCKET, pdf_key, pdf_bytes, "application/pdf")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"PDF upload failed: {exc}")

    try:
        upload_json(OVERLAYS_BUCKET, overlay_key, overlay.model_dump())
    except Exception:
        pass

    needs_review = grade_result.needs_review or needs_review_from_overlay

    update_upload(
        row["id"],
        {
            "status": "graded",
            "needs_review": needs_review,
            "graded_pdf_path": pdf_key,
            "overlay_path": overlay_key,
            "overlay_json": overlay.model_dump(),
            "grade_json": {
                **grade_result.model_dump(),
                "answers": [a.to_dict() for a in answers],
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
