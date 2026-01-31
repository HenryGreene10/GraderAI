from __future__ import annotations

import logging
from datetime import datetime, timezone
from io import BytesIO
from uuid import uuid4

from fastapi import APIRouter, File, HTTPException, UploadFile
from PIL import Image, ImageOps

from ..config import SUBMISSIONS_BUCKET
from ..services import ocr as ocr_service
from ..services.db import require_supabase, update_upload
from ..services.ocr import normalize_ocr_result
from ..services.storage import upload_bytes
from .uploads import run_grade_pipeline

router = APIRouter(prefix="/api/scan", tags=["scan"])
logger = logging.getLogger(__name__)


def _utc_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _parse_ts(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not value:
        return None
    text = str(value)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except Exception:
        return None


def _is_expired(value: object) -> bool:
    ts = _parse_ts(value)
    if not ts:
        return False
    now = datetime.now(timezone.utc)
    return ts <= now


def _load_session(token: str) -> dict:
    sb = require_supabase()
    resp = (
        sb.table("scan_sessions")
        .select("id,token,owner_id,assignment_id,mode,status,expires_at,resulting_upload_id")
        .eq("token", token)
        .maybe_single()
        .execute()
    )
    row = resp.data
    if not row:
        raise HTTPException(status_code=404, detail="scan_session_not_found")
    return row


async def _run_ocr_and_grade(upload_id: str, owner_id: str, image_bytes: bytes) -> None:
    logger.info("scan_ocr_start upload_id=%s", upload_id)
    try:
        raw = await ocr_service.extract_text(image_bytes=image_bytes)
        norm = normalize_ocr_result(raw)
        text = (norm.get("text") or "").strip()
        if not text:
            raise ValueError("OCR returned empty text")
    except Exception as exc:
        error = str(exc) or "OCR failed"
        logger.warning("scan_ocr_failed upload_id=%s error=%s", upload_id, error)
        update_upload(
            upload_id,
            {
                "ocr_status": "error",
                "status": "error",
                "ocr_error": error,
                "needs_review": True,
                "updated_at": _utc_iso(),
            },
        )
        return

    update_upload(
        upload_id,
        {
            "ocr_status": "done",
            "status": "ocr_done",
            "ocr_text": text,
            "extracted_text": text,
            "ocr_boxes": norm.get("boxes"),
            "ocr_confidence": norm.get("confidence"),
            "ocr_error": None,
            "updated_at": _utc_iso(),
        },
    )
    try:
        await run_grade_pipeline(upload_id, owner_id)
    except HTTPException as exc:
        logger.warning("scan_grade_failed upload_id=%s error=%s", upload_id, exc.detail)
    except Exception as exc:
        logger.exception("scan_grade_failed upload_id=%s error=%s", upload_id, exc)
    logger.info("scan_ocr_complete upload_id=%s", upload_id)


@router.get("/{token}/status")
def scan_status(token: str):
    row = _load_session(token)
    status = str(row.get("status") or "pending")
    if _is_expired(row.get("expires_at")) and status == "pending":
        status = "expired"
    return {
        "status": status,
        "mode": row.get("mode"),
        "resulting_upload_id": row.get("resulting_upload_id"),
    }


@router.post("/{token}/upload")
async def scan_upload(token: str, file: UploadFile = File(...)):
    row = _load_session(token)
    status = str(row.get("status") or "pending")
    if status != "pending":
        raise HTTPException(status_code=409, detail="scan_session_not_pending")
    if _is_expired(row.get("expires_at")):
        raise HTTPException(status_code=410, detail="scan_session_expired")
    if not file or not (file.content_type or "").startswith("image/"):
        raise HTTPException(status_code=400, detail="scan_image_required")

    blob = await file.read()
    logger.info(
        "SCAN UPLOAD RECEIVED token=%s content_type=%s size=%s",
        token,
        getattr(file, "content_type", None),
        len(blob),
    )
    if not blob:
        raise HTTPException(status_code=400, detail="scan_image_empty")

    try:
        with Image.open(BytesIO(blob)) as img:
            img = ImageOps.exif_transpose(img).convert("RGB")
            width, height = img.size
            buf = BytesIO()
            img.save(buf, format="PNG")
            png_bytes = buf.getvalue()
    except Exception as exc:
        logger.warning("scan_image_decode_failed token=%s error=%s", token, exc)
        raise HTTPException(status_code=400, detail="scan_image_invalid")

    owner_id = str(row.get("owner_id") or "")
    assignment_id = str(row.get("assignment_id") or "")
    mode = str(row.get("mode") or "")
    session_id = row.get("id")

    logger.info(
        "scan_upload_start session_id=%s mode=%s assignment_id=%s",
        session_id,
        mode,
        assignment_id,
    )

    sb = require_supabase()
    resulting_upload_id = None

    if mode == "master_key":
        key = f"{owner_id}/templates/{assignment_id}.png"
        upload_bytes(SUBMISSIONS_BUCKET, key, png_bytes, "image/png")
        storage_path = f"{SUBMISSIONS_BUCKET}/{key}"
        sb.table("assignments").update(
            {
                "template_storage_path": storage_path,
                "template_width_px": int(width),
                "template_height_px": int(height),
            }
        ).eq("id", assignment_id).execute()
    elif mode == "student":
        upload_id = str(uuid4())
        key = f"{owner_id}/normalized/{upload_id}.png"
        upload_bytes(SUBMISSIONS_BUCKET, key, png_bytes, "image/png")
        storage_path = f"{SUBMISSIONS_BUCKET}/{key}"
        now = _utc_iso()
        sb.table("uploads").insert(
            {
                "id": upload_id,
                "owner_id": owner_id,
                "assignment_id": assignment_id,
                "storage_path": storage_path,
                "original_name": "scan.png",
                "mime_type": "image/png",
                "size_bytes": len(png_bytes),
                "status": "uploading",
                "ocr_status": "pending",
                "normalized_image_path": storage_path,
                "normalized_width_px": int(width),
                "normalized_height_px": int(height),
                "created_at": now,
                "updated_at": now,
            }
        ).execute()
        resulting_upload_id = upload_id
    else:
        raise HTTPException(status_code=400, detail="scan_session_mode_invalid")

    sb.table("scan_sessions").update(
        {"status": "complete", "resulting_upload_id": resulting_upload_id}
    ).eq("id", session_id).execute()

    logger.info(
        "scan_upload_complete session_id=%s mode=%s resulting_upload_id=%s",
        session_id,
        mode,
        resulting_upload_id,
    )

    if mode == "student" and resulting_upload_id:
        await _run_ocr_and_grade(resulting_upload_id, owner_id, png_bytes)

    return {
        "ok": True,
        "mode": mode,
        "assignment_id": assignment_id,
        "resulting_upload_id": resulting_upload_id,
    }
