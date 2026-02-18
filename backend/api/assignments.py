from __future__ import annotations

import json
import logging
import os
import secrets
import time
from datetime import date, datetime, timedelta, timezone
from typing import Literal, Optional
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel
from pypdf import PdfReader

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
from ..services.pdf_raster import extract_pdf_page_raster_png, pdf_page_sizes_px
from ..services.scanner import PDF_DPI
from ..services.template import detect_answer_boxes, detect_question_regions
from ..services.template_manifest import load_template_manifest, with_approved_manifest
from ..services.template_regions import expected_answers_from_regions, parse_regions_payload
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
STUDENT_UPLOAD_EXTS = {".pdf"}
MASTER_KEY_EXTS = {".png", ".jpg", ".jpeg", ".pdf"}


def _qid_sort_key(qid: str) -> tuple[int, str]:
    digits = "".join(ch for ch in str(qid) if ch.isdigit())
    if digits:
        return int(digits), str(qid)
    return 10_000, str(qid)


class AssignmentCreate(BaseModel):
    title: str
    description: Optional[str] = None
    due_date: Optional[date] = None


class ScanSessionCreate(BaseModel):
    mode: Literal["master_key", "student"]


class TemplateManualApproveBody(BaseModel):
    force: bool = False


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


def _template_regions_payload(raw_payload: object) -> object:
    if isinstance(raw_payload, (dict, list)):
        return raw_payload
    if isinstance(raw_payload, str):
        try:
            parsed = json.loads(raw_payload)
        except Exception:
            return {}
        if isinstance(parsed, (dict, list)):
            return parsed
    return {}


def _template_approval_reasons(payload: object) -> list[str]:
    data = payload if isinstance(payload, dict) else {}
    raw_reasons = data.get("manifest_approval_block_reasons")
    if not isinstance(raw_reasons, list):
        return []
    reasons = [str(item).strip() for item in raw_reasons if str(item).strip()]
    return sorted(set(reasons))


def _template_regions_count(payload: object) -> int:
    regions_map, _ = parse_regions_payload(payload)
    return len(regions_map)


def _master_key_status_for_assignment(row: dict, regions_payload: object) -> tuple[str, list[str]]:
    if not row.get("template_storage_path"):
        return "DRAFT", []

    regions_dict = regions_payload if isinstance(regions_payload, dict) else {}
    reasons = _template_approval_reasons(regions_dict)
    regions_count = _template_regions_count(regions_payload)
    manifest_locked = bool(regions_dict.get("manifest_locked"))
    manifest_approved_at = str(regions_dict.get("manifest_approved_at") or "").strip()
    approval_blocked = bool(regions_dict.get("manifest_approval_blocked"))

    status_from_payload = str(regions_dict.get("master_key_status") or "").strip().upper()
    if status_from_payload in {"DRAFT", "PROCESSING", "NEEDS_REUPLOAD", "READY"}:
        status = status_from_payload
    elif manifest_locked and manifest_approved_at and not approval_blocked and not reasons and regions_count > 0:
        status = "READY"
    elif approval_blocked or reasons or regions_count <= 0:
        status = "NEEDS_REUPLOAD"
    else:
        status = "PROCESSING"

    if status == "READY":
        return status, []
    if reasons:
        return status, reasons
    if regions_count <= 0:
        return status, ["TEMPLATE_REGIONS_EMPTY"]
    if not manifest_locked or not manifest_approved_at:
        return status, ["TEMPLATE_MANIFEST_NOT_APPROVED"]
    return status, []


def _extract_master_key_image_bytes(payload: bytes, ext: str, content_type: str | None) -> bytes:
    if ext in {".png", ".jpg", ".jpeg"} or str(content_type or "").lower().startswith("image/"):
        return payload
    if ext != ".pdf" and "pdf" not in str(content_type or "").lower():
        raise HTTPException(status_code=400, detail="template_image_or_pdf_required")
    try:
        reader = PdfReader(BytesIO(payload))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"template_pdf_invalid: {exc}")
    if not reader.pages:
        raise HTTPException(status_code=400, detail="template_pdf_empty")

    def _raster_signal_score(png_bytes: bytes) -> tuple[int, int]:
        box_count = 0
        area_px = 0
        try:
            hints = detect_answer_boxes(png_bytes) or []
            box_count = len(hints)
        except Exception:
            box_count = 0
        try:
            with Image.open(BytesIO(png_bytes)) as img:
                area_px = int(max(1, img.width) * max(1, img.height))
        except Exception:
            area_px = 0
        return box_count, area_px

    candidates: list[tuple[bytes, str, int, int]] = []
    for prefer in ("embedded", "rendered"):
        try:
            raster_png, raster_source = extract_pdf_page_raster_png(
                payload,
                page_index=0,
                dpi=float(PDF_DPI),
                prefer=prefer,  # type: ignore[arg-type]
            )
        except Exception:
            continue
        if not raster_png:
            continue
        boxes, area = _raster_signal_score(raster_png)
        candidates.append((raster_png, raster_source, boxes, area))

    if not candidates:
        raise HTTPException(
            status_code=400,
            detail="template_pdf_raster_failed: could not decode page raster. Upload a scanned PDF (or PNG/JPG).",
        )

    unique_candidates: list[tuple[bytes, str, int, int]] = []
    seen_payloads: set[bytes] = set()
    for candidate in candidates:
        if candidate[0] in seen_payloads:
            continue
        seen_payloads.add(candidate[0])
        unique_candidates.append(candidate)

    source_rank = {"rendered_page": 2, "largest_embedded_raster": 1, "blank_page_fallback": 0}
    chosen = max(
        unique_candidates,
        key=lambda item: (int(item[2]), int(item[3]), int(source_rank.get(item[1], 0))),
    )
    raster_png, raster_source, box_count, area_px = chosen
    logger.info(
        "master_key_pdf_raster_selected source=%s answer_box_hints=%s area_px=%s candidates=%s",
        raster_source,
        box_count,
        area_px,
        [
            {
                "source": src,
                "answer_box_hints": boxes,
                "area_px": area,
            }
            for _png, src, boxes, area in unique_candidates
        ],
    )
    if raster_source == "blank_page_fallback":
        raise HTTPException(status_code=400, detail="template_pdf_raster_failed: Upload a scanned PDF.")
    return raster_png


def _assignment_detail_payload(row: dict) -> dict:
    base = _assignment_payload(row, uploads_count=0)
    regions = _template_regions_payload(row.get("template_regions_json") or {})
    regions_dict = regions if isinstance(regions, dict) else {}
    detected_qids = []
    regions_count = 0
    manifest_locked = bool(regions_dict.get("manifest_locked"))
    manifest_approved_at = str(regions_dict.get("manifest_approved_at") or "").strip() or None
    approval_blocked = bool(regions_dict.get("manifest_approval_blocked"))
    approval_reasons = _template_approval_reasons(regions_dict)
    master_key_status, master_key_status_reasons = _master_key_status_for_assignment(row, regions)
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
            "template_manifest_locked": manifest_locked,
            "template_manifest_approved_at": manifest_approved_at,
            "template_approval_blocked": approval_blocked,
            "template_approval_block_reasons": approval_reasons,
            "template_ready_for_grading": master_key_status == "READY",
            "master_key_status": master_key_status,
            "master_key_status_reasons": master_key_status_reasons,
            "master_key_ready": master_key_status == "READY",
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

    regions_payload = row.get("template_regions_json") or {}
    regions_map, _meta = parse_regions_payload(regions_payload)
    region_answers = expected_answers_from_regions(regions_map) if regions_map else {}
    if region_answers:
        sorted_qids = sorted(region_answers.keys(), key=_qid_sort_key)
        answers = {qid: str(region_answers.get(qid) or "").strip() for qid in sorted_qids}
        prompt_version = "template-regions-v1"
        response = {
            "answers": answers,
            "prompt_version": prompt_version,
        }
        if include_metadata:
            response["metadata"] = {
                "source": "template_regions",
                "regions_full": regions_payload,
                "template_uploaded_at": row.get("template_uploaded_at"),
            }
        return response

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
    if ext not in MASTER_KEY_EXTS:
        raise HTTPException(status_code=400, detail="template_image_or_pdf_only")

    payload = await file.read()
    if not payload:
        raise HTTPException(status_code=400, detail="template_empty")
    payload = _extract_master_key_image_bytes(payload, ext, file.content_type)

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
        "master_key_status": "NEEDS_REUPLOAD" if result.approval_blocked else "READY",
    }


@router.post("/{assignment_id}/template/approve")
def approve_template_manifest(
    assignment_id: str,
    body: TemplateManualApproveBody,
    user_id: str = Depends(get_current_user_id),
):
    if os.getenv("ALLOW_TEMPLATE_MANUAL_APPROVAL") != "1":
        raise HTTPException(status_code=403, detail="manual_template_approval_disabled")

    row = get_assignment(
        assignment_id,
        user_id,
        columns=(
            "id,owner_id,template_storage_path,template_regions_json,template_width_px,"
            "template_height_px,template_version,needs_review"
        ),
    )
    if not row.get("template_storage_path"):
        raise HTTPException(status_code=409, detail="template_missing")

    regions_payload = _template_regions_payload(row.get("template_regions_json") or {})
    regions_map, _ = parse_regions_payload(regions_payload)
    if not regions_map:
        raise HTTPException(status_code=409, detail="template_regions_missing")

    regions_dict = regions_payload if isinstance(regions_payload, dict) else {}
    blocked_reasons = _template_approval_reasons(regions_dict)
    approval_blocked = bool(regions_dict.get("manifest_approval_blocked"))
    if approval_blocked and not body.force:
        joined = ", ".join(blocked_reasons) if blocked_reasons else "unknown_reasons"
        raise HTTPException(status_code=409, detail=f"template_manifest_blocked: {joined}")

    approved_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    approved_payload = with_approved_manifest(
        regions_payload,
        template_version=int(row.get("template_version") or 1),
        template_width_px=row.get("template_width_px"),
        template_height_px=row.get("template_height_px"),
        approved_at=approved_at,
    )
    approved_payload["manifest_approval_blocked"] = False
    approved_payload["manifest_approval_block_reasons"] = []
    approved_payload["manifest_manual_approved"] = True
    approved_payload["manifest_manual_approved_at"] = approved_at
    approved_payload["manifest_manual_approved_by"] = user_id
    if blocked_reasons:
        approved_payload["manifest_manual_approved_from_reasons"] = blocked_reasons

    sb = require_supabase()
    sb.table("assignments").update(
        {
            "template_regions_json": approved_payload,
            "needs_review": True if blocked_reasons else bool(row.get("needs_review")),
        }
    ).eq("id", assignment_id).execute()

    return {
        "ok": True,
        "assignment_id": assignment_id,
        "manifest_locked": True,
        "manifest_approved_at": approved_at,
        "manual_approved": True,
        "forced": bool(body.force),
        "prior_block_reasons": blocked_reasons,
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
        columns=(
            "id,owner_id,template_storage_path,template_regions_json,template_upload_id,template_version,"
            "template_width_px,template_height_px"
        ),
    )

    template_regions = _template_regions_payload(assignment.get("template_regions_json") or {})
    template_storage_path = assignment.get("template_storage_path")
    regions_map, _ = parse_regions_payload(template_regions)
    if not template_storage_path or not regions_map:
        raise HTTPException(status_code=409, detail="Upload master key first.")
    try:
        load_template_manifest(
            template_regions,
            template_version=int(assignment.get("template_version") or 1),
            template_width_px=assignment.get("template_width_px"),
            template_height_px=assignment.get("template_height_px"),
            require_approved=True,
        )
    except Exception as exc:
        reasons = _template_approval_reasons(template_regions)
        reasons_txt = f" ({', '.join(reasons)})" if reasons else ""
        raise HTTPException(
            status_code=409,
            detail=f"Master key is not READY. Reupload a clean scanned PDF. {exc}{reasons_txt}",
        )

    if not files:
        raise HTTPException(status_code=400, detail="files_required")

    sb = require_supabase()
    rows = []
    rel_keys: list[str] = []

    for file in files:
        ext = _file_extension(file.filename, file.content_type)
        if ext not in STUDENT_UPLOAD_EXTS:
            raise HTTPException(
                status_code=400,
                detail=f"student_upload_pdf_required: {file.filename}",
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
            page_sizes = pdf_page_sizes_px(blob, dpi=float(PDF_DPI))
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"invalid_pdf: {file.filename}: {exc}")
        first_page = page_sizes[0] if page_sizes else {}
        normalized_width_px = int(first_page.get("width_px") or 0) or None
        normalized_height_px = int(first_page.get("height_px") or 0) or None

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
                "page_count": len(page_sizes) if page_sizes else None,
                "page_sizes_json": page_sizes or None,
                "normalized_pdf_path": storage_path,
                "normalized_width_px": normalized_width_px,
                "normalized_height_px": normalized_height_px,
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
