from __future__ import annotations

import logging
import os
from datetime import date
from typing import Optional
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel

from ..auth import get_current_user_id
from ..config import GRADED_BUCKET, OVERLAYS_BUCKET, SUBMISSIONS_BUCKET
from ..services.db import get_assignment, require_supabase
from ..services.storage import strip_bucket_prefix, upload_bytes
from ..services.scanner import normalize_image_bytes
from ..services import ocr as ocr_service
from ..services.template import extract_template_regions
from .ocr import run_ocr_for_upload

router = APIRouter(prefix="/api/assignments", tags=["assignments"])
logger = logging.getLogger(__name__)

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
    regions = row.get("template_regions_json") or []
    detected_qids = []
    if isinstance(regions, list):
        detected_qids = [str(r.get("qid")) for r in regions if isinstance(r, dict) and r.get("qid")]
    base.update(
        {
            "template_storage_path": row.get("template_storage_path"),
            "template_regions_count": len(regions) if isinstance(regions, list) else 0,
            "template_detected_qids": detected_qids,
            "template_width_px": row.get("template_width_px"),
            "template_height_px": row.get("template_height_px"),
            "template_version": row.get("template_version"),
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
            "template_height_px,template_version"
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
            overlay_keys.append(strip_bucket_prefix(overlay_path, OVERLAYS_BUCKET))

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
    assignment = get_assignment(
        assignment_id,
        user_id,
        columns="id,owner_id,template_version",
    )
    ext = _file_extension(file.filename, file.content_type)
    if ext not in {".png", ".jpg", ".jpeg"}:
        raise HTTPException(status_code=400, detail="template_image_only")

    payload = await file.read()
    if not payload:
        raise HTTPException(status_code=400, detail="template_empty")

    scan = normalize_image_bytes(payload)
    owner_id = assignment.get("owner_id") or user_id or "unknown"
    template_key = f"{owner_id}/templates/{assignment_id}.png"
    upload_bytes(SUBMISSIONS_BUCKET, template_key, scan.normalized_png, "image/png")

    try:
        regions = await extract_template_regions(scan.normalized_png, ocr_service.extract_text)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    template_regions = [
        {
            "qid": region.qid,
            "region": {"x": region.region[0], "y": region.region[1], "w": region.region[2], "h": region.region[3]},
            "answer_box": {
                "x": region.answer_box[0],
                "y": region.answer_box[1],
                "w": region.answer_box[2],
                "h": region.answer_box[3],
            },
            "expected_answer_text": region.expected_answer_text,
            "label_method": region.label_method,
            "index": region.index,
        }
        for region in regions
    ]

    template_version = int(assignment.get("template_version") or 0) + 1
    sb = require_supabase()
    sb.table("assignments").update(
        {
            "template_storage_path": f"{SUBMISSIONS_BUCKET}/{template_key}",
            "template_width_px": scan.width_px,
            "template_height_px": scan.height_px,
            "template_regions_json": template_regions,
            "template_version": template_version,
        }
    ).eq("id", assignment_id).execute()

    return {
        "ok": True,
        "assignment_id": assignment_id,
        "template_storage_path": f"{SUBMISSIONS_BUCKET}/{template_key}",
        "template_version": template_version,
        "boxes_detected": len(template_regions),
        "qids": [r["qid"] for r in template_regions],
    }


@router.post("/{assignment_id}/uploads")
async def upload_assignment_files(
    assignment_id: str,
    background_tasks: BackgroundTasks,
    files: list[UploadFile] = File(...),
    user_id: str = Depends(get_current_user_id),
):
    get_assignment(assignment_id, user_id, columns="id,owner_id")

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
        await run_ocr_for_upload(upload_id, user_id)
    except HTTPException as exc:
        logger.warning("OCR failed for upload %s: %s", upload_id, exc.detail)
    except Exception:
        logger.exception("OCR failed for upload %s", upload_id)
