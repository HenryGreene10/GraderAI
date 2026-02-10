from __future__ import annotations

import logging
import os
import secrets
import time
from datetime import date, datetime, timedelta, timezone
from typing import Literal, Optional
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel

from ..auth import get_current_user_id
from ..config import GRADED_BUCKET, OVERLAYS_BUCKET, SUBMISSIONS_BUCKET
from ..services.db import get_assignment, require_supabase
from ..services.storage import download_submission_bytes, normalize_storage_path, strip_bucket_prefix, upload_bytes
from io import BytesIO

from PIL import Image

from ..services import ocr as ocr_service
from ..services.ocr import normalize_ocr_result
from ..services.answer_extraction import extract_answers_from_ocr
from ..services.master_key_pipeline import run_master_key_approval_pipeline
from ..services.template import detect_answer_boxes, detect_question_regions
from ..services.template_manifest import load_template_manifest
from ..services.template_regions import parse_regions_payload
from .ocr import run_ocr_for_upload

router = APIRouter(prefix="/api/assignments", tags=["assignments"])

logger = logging.getLogger(__name__)

try:  # pragma: no cover - optional postgrest error typing
    from postgrest.exceptions import APIError
except Exception:  # pragma: no cover
    APIError = Exception  # type: ignore

ALLOWED_EXTS = {".png", ".jpg", ".jpeg", ".pdf"}
ALLOWED_MIME = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "application/pdf": ".pdf",
}


class AssignmentCreate(BaseModel):
    title: str
    description: Optional[str] = None
    due_date: Optional[date] = None


class ScanSessionCreate(BaseModel):
    mode: Literal["master_key", "student"]


def _scan_sessions_missing(exc: Exception) -> bool:
    err = getattr(exc, "message", None)
    if isinstance(err, dict):
        code = str(err.get("code") or "")
        msg = str(err.get("message") or err.get("details") or "")
        if code in {"PGRST205", "PGRST204", "42P01"}:
            return True
        lower = msg.lower()
        if "scan_sessions" in lower and ("schema cache" in lower or "does not exist" in lower or "not found" in lower):
            return True
    text = str(exc).lower()
    return "scan_sessions" in text and ("schema cache" in text or "does not exist" in text or "not found" in text)


def _assignment_payload(row: dict, uploads_count: int = 0) -> dict:
    rubric = row.get("rubric_json") or {}
    return {
        "id": row.get("id"),
        "title": row.get("title"),
        "description": rubric.get("description"),
        "due_date": row.get("due_date"),
        "created_at": row.get("created_at"),
        "uploads_count": uploads_count,
    }


def _assignment_detail_payload(row: dict) -> dict:
    base = _assignment_payload(row, uploads_count=0)
    regions = row.get("template_regions_json") or {}
    detected_qids = []
    regions_count = 0
    try:
        manifest, _embedded = load_template_manifest(
            regions,
            template_version=int(row.get("template_version") or 1),
            template_width_px=row.get("template_width_px"),
            template_height_px=row.get("template_height_px"),
            require_approved=False,
        )
        detected_qids = [q.question_id for q in manifest.questions]
        regions_count = int(manifest.question_count or len(detected_qids))
    except Exception:
        regions_map, _ = parse_regions_payload(regions)
        detected_qids = list(regions_map.keys()) if regions_map else []
        regions_count = len(detected_qids)
    base.update(
        {
            "template_storage_path": row.get("template_storage_path"),
            "template_regions_count": regions_count,
            "template_detected_qids": detected_qids,
            "template_width_px": row.get("template_width_px"),
            "template_height_px": row.get("template_height_px"),
            "template_version": row.get("template_version"),
            "template_upload_id": row.get("template_upload_id"),
            "template_original_name": row.get("template_original_name"),
            "template_uploaded_at": row.get("template_uploaded_at"),
        }
    )
    return base


def _file_extension(filename: Optional[str], content_type: Optional[str]) -> str:
    ext = os.path.splitext(filename or "")[1].lower()
    if ext in ALLOWED_EXTS:
        return ext
    if content_type in ALLOWED_MIME:
        return ALLOWED_MIME[content_type]
    return ""


def _template_debug_enabled() -> bool:
    return os.getenv("TEMPLATE_DEBUG_OVERLAY") == "1"


def _save_template_debug_overlay(
    template_png: bytes,
    assignment_id: str,
    regions: list[tuple[float, float, float, float]],
    answer_boxes: list[tuple[float, float, float, float]],
) -> str:
    from PIL import ImageDraw

    image = Image.open(BytesIO(template_png)).convert("RGB")
    draw = ImageDraw.Draw(image)
    for idx, (x, y, w, h) in enumerate(regions, start=1):
        draw.rectangle([x, y, x + w, y + h], outline=(0, 120, 255), width=3)
        draw.text((x + 6, y + 6), f"R{idx}", fill=(0, 120, 255))
    for idx, (x, y, w, h) in enumerate(answer_boxes, start=1):
        draw.rectangle([x, y, x + w, y + h], outline=(220, 0, 0), width=3)
        draw.text((x + 6, y + 6), f"A{idx}", fill=(220, 0, 0))

    tmp_dir = os.path.join(os.path.dirname(__file__), "..", "tmp")
    os.makedirs(tmp_dir, exist_ok=True)
    filename = f"template_overlay_{assignment_id}_{int(time.time())}.png"
    path = os.path.join(tmp_dir, filename)
    image.save(path, format="PNG")
    return path


@router.get("")
def list_assignments(user_id: str = Depends(get_current_user_id)):
    sb = require_supabase()
    resp = (
        sb.table("assignments")
        .select("id,title,due_date,created_at,rubric_json")
        .eq("owner_id", user_id)
        .order("created_at", desc=True)
        .execute()
    )
    rows = resp.data or []

    counts: dict[str, int] = {}
    try:
        uploads_resp = (
            sb.table("uploads")
            .select("assignment_id")
            .eq("owner_id", user_id)
            .execute()
        )
        for row in uploads_resp.data or []:
            aid = row.get("assignment_id")
            if aid:
                counts[str(aid)] = counts.get(str(aid), 0) + 1
    except Exception:
        counts = {}

    return {
        "assignments": [
            _assignment_payload(row, counts.get(str(row.get("id")), 0))
            for row in rows
        ]
    }


@router.get("/{assignment_id}")
def get_assignment_detail(
    assignment_id: str,
    user_id: str = Depends(get_current_user_id),
):
    row = get_assignment(
        assignment_id,
        user_id,
        columns=(
            "id,owner_id,title,due_date,created_at,rubric_json,"
            "template_storage_path,template_regions_json,template_width_px,"
            "template_height_px,template_version,template_upload_id,"
            "template_original_name,template_uploaded_at"
        ),
    )
    return {"assignment": _assignment_detail_payload(row)}


@router.post("")
def create_assignment(
    body: AssignmentCreate,
    user_id: str = Depends(get_current_user_id),
):
    title = (body.title or "").strip()
    if not title:
        raise HTTPException(status_code=400, detail="title_required")

    payload = {
        "owner_id": user_id,
        "title": title,
        "due_date": body.due_date,
    }
    if body.description:
        payload["rubric_json"] = {"description": body.description.strip()}

    sb = require_supabase()
    try:
        resp = sb.table("assignments").insert(payload).execute()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"db_insert_failed: {exc}")

    row = None
    if isinstance(resp.data, list) and resp.data:
        row = resp.data[0]
    if not row:
        row = payload

    return {"assignment": _assignment_payload(row, uploads_count=0)}


@router.post("/{assignment_id}/scan-sessions")
def create_scan_session(
    assignment_id: str,
    body: ScanSessionCreate,
    user_id: str = Depends(get_current_user_id),
):
    assignment = get_assignment(assignment_id, user_id, columns="id,owner_id")
    owner_id = assignment.get("owner_id") or user_id
    token = secrets.token_urlsafe(16)
    session_id = str(uuid4())
    expires_at = (datetime.now(timezone.utc) + timedelta(minutes=15)).replace(microsecond=0)
    expires_at_iso = expires_at.isoformat().replace("+00:00", "Z")
    payload = {
        "id": session_id,
        "token": token,
        "owner_id": owner_id,
        "assignment_id": assignment_id,
        "mode": body.mode,
        "status": "pending",
        "expires_at": expires_at_iso,
    }
    sb = require_supabase()
    try:
        sb.table("scan_sessions").insert(payload).execute()
    except APIError as exc:
        if _scan_sessions_missing(exc):
            raise HTTPException(
                status_code=400,
                detail=(
                    "scan_sessions table missing. Apply migrations/2026-01-30_add_scan_sessions.sql "
                    "to your Supabase project and reload schema cache."
                ),
            )
        raise HTTPException(status_code=500, detail=f"scan_session_create_failed: {exc}")
    except Exception as exc:
        if _scan_sessions_missing(exc):
            raise HTTPException(
                status_code=400,
                detail=(
                    "scan_sessions table missing. Apply migrations/2026-01-30_add_scan_sessions.sql "
                    "to your Supabase project and reload schema cache."
                ),
            )
        raise HTTPException(status_code=500, detail=f"scan_session_create_failed: {exc}")
    logger.info(
        "scan_session_created assignment_id=%s owner_id=%s mode=%s session_id=%s expires_at=%s",
        assignment_id,
        owner_id,
        body.mode,
        session_id,
        expires_at_iso,
    )
    return {"token": token, "expires_at": expires_at_iso}


@router.get("/{assignment_id}/uploads")
def list_assignment_uploads(
    assignment_id: str,
    user_id: str = Depends(get_current_user_id),
):
    get_assignment(assignment_id, user_id, columns="id,owner_id")
    sb = require_supabase()
    resp = (
        sb.table("uploads")
        .select(
            "id,storage_path,original_name,mime_type,size_bytes,status,created_at,"
            "ocr_status,ocr_error,graded_pdf_path,needs_review"
        )
        .eq("owner_id", user_id)
        .eq("assignment_id", assignment_id)
        .order("created_at", desc=True)
        .execute()
    )
    rows = resp.data or []
    uploads = []
    for row in rows:
        uploads.append(
            {
                "id": row.get("id"),
                "storage_path": row.get("storage_path"),
                "original_name": row.get("original_name"),
                "mime_type": row.get("mime_type"),
                "size_bytes": row.get("size_bytes"),
                "status": row.get("status"),
                "ocr_status": row.get("ocr_status"),
                "ocr_error": row.get("ocr_error"),
                "graded_pdf_path": row.get("graded_pdf_path"),
                "needs_review": row.get("needs_review"),
                "created_at": row.get("created_at"),
            }
        )
    return {"uploads": uploads}


@router.get("/{assignment_id}/template-ocr")
async def get_template_ocr(
    assignment_id: str,
    user_id: str = Depends(get_current_user_id),
    include_raw: bool = Query(False),
):
    row = get_assignment(
        assignment_id,
        user_id,
        columns="id,owner_id,template_storage_path,template_regions_json,template_uploaded_at",
    )
    regions_payload = row.get("template_regions_json") or []
    regions_map, _ = parse_regions_payload(regions_payload)
    if not row.get("template_storage_path"):
        raise HTTPException(status_code=404, detail="template_missing")
    payload_regions = []
    for qid, region in regions_map.items():
        payload_regions.append(
            {
                "qid": qid,
                "expected_answer_text": region.get("expected_answer_text"),
            }
        )
    response = {
        "template_uploaded_at": row.get("template_uploaded_at"),
        "regions": payload_regions,
        "regions_full": regions_payload,
        "count": len(payload_regions),
        "template_has_regions": bool(payload_regions),
    }
    if include_raw:
        template_bytes = download_submission_bytes(row.get("template_storage_path"))
        raw = ocr_service.extract_text(image_bytes=template_bytes)
        if hasattr(raw, "__await__"):
            raw = await raw
        response["raw_ocr"] = normalize_ocr_result(raw)
    return response


@router.get("/{assignment_id}/template-regions")
def get_template_regions(
    assignment_id: str,
    user_id: str = Depends(get_current_user_id),
):
    row = get_assignment(
        assignment_id,
        user_id,
        columns="id,owner_id,template_regions_json,template_uploaded_at",
    )
    regions_payload = row.get("template_regions_json") or {}
    regions_map, _ = parse_regions_payload(regions_payload)
    return {
        "template_uploaded_at": row.get("template_uploaded_at"),
        "regions": regions_payload,
        "count": len(regions_map),
    }


@router.get("/{assignment_id}/answer-key")
async def get_answer_key(
    assignment_id: str,
    user_id: str = Depends(get_current_user_id),
    include_metadata: bool = Query(False),
):
    row = get_assignment(
        assignment_id,
        user_id,
        columns="id,owner_id,template_storage_path,template_regions_json,template_uploaded_at",
    )
    if not row.get("template_storage_path"):
        raise HTTPException(status_code=404, detail="template_missing")

    template_bytes = download_submission_bytes(row.get("template_storage_path"))
    raw = ocr_service.extract_text(image_bytes=template_bytes)
    if hasattr(raw, "__await__"):
        raw = await raw
    norm = normalize_ocr_result(raw)
    ocr_text = str(norm.get("text") or "").strip()
    if not ocr_text:
        raise HTTPException(status_code=400, detail="template_ocr_empty")

    answers, prompt_version = await extract_answers_from_ocr(ocr_text, role="answer_key")
    response = {
        "answers": answers,
        "prompt_version": prompt_version,
    }
    if include_metadata:
        response["metadata"] = {
            "ocr_text": ocr_text,
            "raw_ocr": norm,
            "regions_full": row.get("template_regions_json") or [],
            "template_uploaded_at": row.get("template_uploaded_at"),
        }
    return response


@router.delete("/{assignment_id}")
def delete_assignment(
    assignment_id: str,
    user_id: str = Depends(get_current_user_id),
):
    get_assignment(assignment_id, user_id, columns="id,owner_id")
    sb = require_supabase()

    resp = (
        sb.table("uploads")
        .select("id,storage_path,graded_pdf_path,overlay_path")
        .eq("owner_id", user_id)
        .eq("assignment_id", assignment_id)
        .execute()
    )
    rows = resp.data or []

    submission_keys = []
    graded_keys = []
    overlay_keys = []

    for row in rows:
        storage_path = row.get("storage_path")
        if storage_path:
            submission_keys.append(strip_bucket_prefix(storage_path, SUBMISSIONS_BUCKET))
        graded_path = row.get("graded_pdf_path")
        if graded_path:
            graded_keys.append(strip_bucket_prefix(graded_path, GRADED_BUCKET))
        overlay_path = row.get("overlay_path")
        if overlay_path:
            normalized = normalize_storage_path(OVERLAYS_BUCKET, overlay_path)
            if normalized:
                overlay_keys.append(normalized)

    try:
        if submission_keys:
            sb.storage.from_(SUBMISSIONS_BUCKET).remove(submission_keys)
        if graded_keys:
            sb.storage.from_(GRADED_BUCKET).remove(graded_keys)
        if overlay_keys:
            sb.storage.from_(OVERLAYS_BUCKET).remove(overlay_keys)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"storage_delete_failed: {exc}")

    try:
        sb.table("uploads").delete().eq("assignment_id", assignment_id).eq("owner_id", user_id).execute()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"uploads_delete_failed: {exc}")

    try:
        sb.table("assignments").delete().eq("id", assignment_id).eq("owner_id", user_id).execute()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"assignment_delete_failed: {exc}")

    return {"ok": True, "assignment_id": assignment_id, "uploads_deleted": len(rows)}


@router.post("/{assignment_id}/template")
async def upload_template(
    assignment_id: str,
    file: UploadFile = File(...),
    user_id: str = Depends(get_current_user_id),
):
    ext = _file_extension(file.filename, file.content_type)
    if ext not in {".png", ".jpg", ".jpeg"}:
        raise HTTPException(status_code=400, detail="template_image_only")

    payload = await file.read()
    if not payload:
        raise HTTPException(status_code=400, detail="template_empty")

    def _debug_hook(template_png: bytes, aid: str) -> None:
        if not _template_debug_enabled():
            return
        debug_regions = detect_question_regions(template_png)
        debug_answers = detect_answer_boxes(template_png)
        overlay_path = _save_template_debug_overlay(template_png, aid, debug_regions, debug_answers)
        logger.info(
            "template_debug_overlay_saved assignment_id=%s path=%s regions=%s answers=%s",
            aid,
            overlay_path,
            len(debug_regions),
            len(debug_answers),
        )

    result = await run_master_key_approval_pipeline(
        assignment_id=assignment_id,
        user_id=user_id,
        payload=payload,
        template_original_name=file.filename,
        debug_hook=_debug_hook,
    )

    return {
        "ok": True,
        "assignment_id": result.assignment_id,
        "template_storage_path": result.template_storage_path,
        "template_version": result.template_version,
        "template_upload_id": result.template_upload_id,
        "template_original_name": result.template_original_name,
        "template_uploaded_at": result.template_uploaded_at,
        "boxes_detected": result.boxes_detected,
        "qids": result.qids,
        "warnings": result.warnings,
        "anchor_trace": result.anchor_trace,
        "approval_blocked": result.approval_blocked,
        "approval_warning": result.approval_warning,
    }


@router.post("/{assignment_id}/uploads")
async def upload_assignment_files(
    assignment_id: str,
    background_tasks: BackgroundTasks,
    files: list[UploadFile] = File(...),
    user_id: str = Depends(get_current_user_id),
):
    assignment = get_assignment(
        assignment_id,
        user_id,
        columns="id,owner_id,template_storage_path,template_regions_json,template_upload_id",
    )

    template_regions = assignment.get("template_regions_json") or []
    template_storage_path = assignment.get("template_storage_path")
    regions_map, _ = parse_regions_payload(template_regions)
    if not template_storage_path or not regions_map:
        raise HTTPException(status_code=409, detail="Upload master key first.")

    if not files:
        raise HTTPException(status_code=400, detail="files_required")

    sb = require_supabase()
    rows = []
    rel_keys: list[str] = []

    for file in files:
        ext = _file_extension(file.filename, file.content_type)
        if ext not in ALLOWED_EXTS:
            raise HTTPException(
                status_code=400,
                detail=f"unsupported_file_type: {file.filename}",
            )
        upload_id = str(uuid4())
        rel_key = f"{user_id}/{assignment_id}/{upload_id}{ext}"
        storage_path = f"{SUBMISSIONS_BUCKET}/{rel_key}"

        blob = await file.read()
        size_bytes = len(blob or b"")
        if size_bytes == 0:
            raise HTTPException(
                status_code=400,
                detail=f"empty_file: {file.filename}",
            )

        try:
            upload_bytes(
                SUBMISSIONS_BUCKET,
                rel_key,
                blob,
                file.content_type or "application/octet-stream",
            )
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"upload_failed: {exc}")

        rel_keys.append(rel_key)
        rows.append(
            {
                "id": upload_id,
                "owner_id": user_id,
                "assignment_id": assignment_id,
                "storage_path": storage_path,
                "original_name": file.filename,
                "mime_type": file.content_type,
                "size_bytes": size_bytes,
                "status": "uploading",
                "ocr_status": "pending",
            }
        )

    try:
        resp = sb.table("uploads").insert(rows).execute()
    except Exception as exc:
        try:
            sb.storage.from_(SUBMISSIONS_BUCKET).remove(rel_keys)
        except Exception:
            pass
        raise HTTPException(status_code=500, detail=f"db_insert_failed: {exc}")

    saved_rows = resp.data or rows

    for row in saved_rows:
        background_tasks.add_task(_run_ocr_in_background, row["id"], user_id)

    return {"uploads": saved_rows}


async def _run_ocr_in_background(upload_id: str, user_id: str) -> None:
    try:
        await run_ocr_for_upload(upload_id, user_id, pipeline_source="assignments.auto")
    except HTTPException as exc:
        logger.warning("OCR failed for upload %s: %s", upload_id, exc.detail)
    except Exception:
        logger.exception("OCR failed for upload %s", upload_id)
