from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from io import BytesIO
from uuid import uuid4

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from PIL import Image
from pypdf import PdfReader

from ..config import SUBMISSIONS_BUCKET
from ..services.db import require_supabase
from ..services.master_key_pipeline import run_master_key_approval_pipeline
from ..services.scanner import PDF_DPI
from ..services.storage import upload_bytes

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


async def _run_ocr_and_grade(upload_id: str, owner_id: str) -> None:
    logger.info("scan_ocr_start upload_id=%s", upload_id)
    try:
        from .ocr import run_ocr_for_upload

        await run_ocr_for_upload(upload_id, owner_id, pipeline_source="scan.auto")
    except HTTPException as exc:
        logger.warning("scan_grade_failed upload_id=%s error=%s", upload_id, exc.detail)
    except Exception as exc:
        logger.exception("scan_grade_failed upload_id=%s error=%s", upload_id, exc)
    logger.info("scan_ocr_complete upload_id=%s", upload_id)


@router.get("/{token}/status")
def scan_status(token: str):
    row = _load_session(token)
    status = str(row.get("status") or "pending")
    if _is_expired(row.get("expires_at")) and status in {"pending", "active"}:
        status = "expired"
    return {
        "status": status,
        "mode": row.get("mode"),
        "resulting_upload_id": row.get("resulting_upload_id"),
    }


def _parse_page_sizes(value: str | None) -> list[dict] | None:
    if not value:
        return None
    try:
        data = json.loads(value)
    except Exception:
        return None
    if not isinstance(data, list):
        return None
    parsed = []
    for item in data:
        width = None
        height = None
        if isinstance(item, dict):
            width = item.get("width_px") or item.get("width") or item.get("w")
            height = item.get("height_px") or item.get("height") or item.get("h")
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            width, height = item[0], item[1]
        try:
            width = int(width)
            height = int(height)
        except Exception:
            continue
        if width > 0 and height > 0:
            parsed.append({"width_px": width, "height_px": height})
    return parsed or None


def _page_sizes_from_pdf(pdf_bytes: bytes) -> list[dict]:
    reader = PdfReader(BytesIO(pdf_bytes))
    sizes = []
    for page in reader.pages:
        box = page.mediabox
        try:
            width_pt = float(box.width)
            height_pt = float(box.height)
        except Exception:
            continue
        width_px = max(1, int(round((width_pt / 72.0) * float(PDF_DPI))))
        height_px = max(1, int(round((height_pt / 72.0) * float(PDF_DPI))))
        sizes.append({"width_px": width_px, "height_px": height_px})
    return sizes


@router.post("/{token}/upload")
async def scan_upload(
    token: str,
    file: UploadFile = File(...),
    normalized_image: UploadFile | None = File(None),
    page_count: int | None = Form(None),
    page_sizes: str | None = Form(None),
):
    row = _load_session(token)
    mode = str(row.get("mode") or "")
    status = str(row.get("status") or "pending")
    if mode == "student":
        if status not in {"pending", "active"}:
            raise HTTPException(status_code=409, detail="scan_session_not_pending")
    elif status != "pending":
        raise HTTPException(status_code=409, detail="scan_session_not_pending")
    if _is_expired(row.get("expires_at")):
        raise HTTPException(status_code=410, detail="scan_session_expired")
    if not file:
        raise HTTPException(status_code=400, detail="scan_file_required")
    content_type = (file.content_type or "").lower()
    filename = (file.filename or "").lower()
    if mode == "student":
        if "pdf" not in content_type and not filename.endswith(".pdf"):
            raise HTTPException(status_code=400, detail="scan_pdf_required")
    else:
        if not content_type.startswith("image/"):
            raise HTTPException(status_code=400, detail="scan_image_required")

    blob = await file.read()
    logger.info(
        "SCAN UPLOAD RECEIVED token=%s content_type=%s size=%s",
        token,
        getattr(file, "content_type", None),
        len(blob),
    )
    if not blob:
        raise HTTPException(status_code=400, detail="scan_file_empty")

    owner_id = str(row.get("owner_id") or "")
    assignment_id = str(row.get("assignment_id") or "")
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
        result = await run_master_key_approval_pipeline(
            assignment_id=assignment_id,
            user_id=owner_id,
            payload=blob,
            template_original_name=file.filename or None,
        )
    elif mode == "student":
        parsed_sizes = _parse_page_sizes(page_sizes)
        if parsed_sizes is None:
            try:
                parsed_sizes = _page_sizes_from_pdf(blob)
            except Exception as exc:
                logger.warning("scan_pdf_parse_failed token=%s error=%s", token, exc)
                parsed_sizes = []
        if page_count is None:
            page_count = len(parsed_sizes) if parsed_sizes else None
        require_normalized_image = os.getenv("SCAN_REQUIRE_NORMALIZED_IMAGE", "1") == "1"
        if require_normalized_image and normalized_image is None:
            raise HTTPException(status_code=400, detail="scan_normalized_image_required")

        upload_id = str(uuid4())
        normalized_image_path = None
        normalized_width_px = None
        normalized_height_px = None
        scan_status = "fallback"
        scan_error = None
        normalized_png: bytes | None = None

        if normalized_image is not None:
            try:
                image_blob = await normalized_image.read()
                if not image_blob:
                    raise ValueError("normalized_image_empty")
                with Image.open(BytesIO(image_blob)) as img:
                    rgb = img.convert("RGB")
                    normalized_width_px = int(rgb.width)
                    normalized_height_px = int(rgb.height)
                    out = BytesIO()
                    rgb.save(out, format="PNG")
                    normalized_png = out.getvalue()
                    if not normalized_png:
                        raise ValueError("normalized_image_convert_failed")
                    scan_status = "normalized"
            except HTTPException:
                raise
            except Exception as exc:
                if require_normalized_image:
                    raise HTTPException(status_code=400, detail=f"scan_normalized_image_invalid:{exc}")
                scan_error = f"normalized_image_invalid:{exc}"

        if require_normalized_image and normalized_png is None:
            raise HTTPException(status_code=400, detail="scan_normalized_image_required")
        if parsed_sizes and normalized_width_px and normalized_height_px:
            parsed_sizes[0] = {
                "width_px": int(normalized_width_px),
                "height_px": int(normalized_height_px),
            }

        key = f"{owner_id}/{upload_id}.pdf"
        upload_bytes(SUBMISSIONS_BUCKET, key, blob, "application/pdf")
        storage_path = f"{SUBMISSIONS_BUCKET}/{key}"
        if normalized_png:
            normalized_key = f"{owner_id}/normalized/{upload_id}.png"
            upload_bytes(SUBMISSIONS_BUCKET, normalized_key, normalized_png, "image/png")
            normalized_image_path = f"{SUBMISSIONS_BUCKET}/{normalized_key}"
        now = _utc_iso()
        sb.table("uploads").insert(
            {
                "id": upload_id,
                "owner_id": owner_id,
                "assignment_id": assignment_id,
                "storage_path": storage_path,
                "original_name": "scan.pdf",
                "mime_type": "application/pdf",
                "size_bytes": len(blob),
                "status": "uploading",
                "ocr_status": "pending",
                "page_count": page_count,
                "page_sizes_json": parsed_sizes or None,
                "normalized_image_path": normalized_image_path,
                "normalized_pdf_path": storage_path,
                "normalized_width_px": normalized_width_px,
                "normalized_height_px": normalized_height_px,
                "scan_status": scan_status,
                "scan_error": scan_error,
                "created_at": now,
                "updated_at": now,
            }
        ).execute()
        resulting_upload_id = upload_id
        asyncio.create_task(_run_ocr_and_grade(upload_id, owner_id))
    else:
        raise HTTPException(status_code=400, detail="scan_session_mode_invalid")

    session_update = {"resulting_upload_id": resulting_upload_id}
    if mode == "master_key":
        session_update["status"] = "complete"
    else:
        session_update["status"] = "active"
    sb.table("scan_sessions").update(session_update).eq("id", session_id).execute()

    logger.info(
        "scan_upload_complete session_id=%s mode=%s resulting_upload_id=%s",
        session_id,
        mode,
        resulting_upload_id,
    )

    return {
        "ok": True,
        "mode": mode,
        "assignment_id": assignment_id,
        "resulting_upload_id": resulting_upload_id,
        "template_version": result.template_version if mode == "master_key" else None,
        "template_upload_id": result.template_upload_id if mode == "master_key" else None,
        "anchor_trace": result.anchor_trace if mode == "master_key" else None,
        "approval_blocked": result.approval_blocked if mode == "master_key" else None,
        "approval_warning": result.approval_warning if mode == "master_key" else None,
        "warnings": result.warnings if mode == "master_key" else [],
    }
