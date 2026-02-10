from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO
from typing import Callable
from uuid import uuid4

from fastapi import HTTPException
from PIL import Image

from ..config import SUBMISSIONS_BUCKET
from . import ocr as ocr_service
from .db import get_assignment, require_supabase
from .ocr import normalize_ocr_result
from .scanner import MAX_DIM_PX, normalize_image_bytes
from .storage import upload_bytes
from .template_anchor_regions import build_anchor_template_regions
from .template_manifest import with_approved_manifest
from .template_regions import build_template_regions_payload

logger = logging.getLogger(__name__)

TEMPLATE_MIN_LONG_EDGE = 1200


@dataclass
class MasterKeyApprovalResult:
    assignment_id: str
    template_storage_path: str
    template_version: int
    template_upload_id: str
    template_original_name: str | None
    template_uploaded_at: str
    boxes_detected: int
    qids: list[str]
    warnings: list[str]


def _utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _normalize_template_image(payload: bytes) -> tuple[bytes, int, int]:
    scan = normalize_image_bytes(payload)
    template_png = scan.normalized_png
    template_w = scan.width_px
    template_h = scan.height_px
    max_dim = max(template_w, template_h)
    if max_dim and max_dim < TEMPLATE_MIN_LONG_EDGE:
        scale = min(float(TEMPLATE_MIN_LONG_EDGE) / float(max_dim), MAX_DIM_PX / float(max_dim))
        new_w = max(1, int(round(template_w * scale)))
        new_h = max(1, int(round(template_h * scale)))
        image = Image.open(BytesIO(template_png)).convert("RGB")
        image = image.resize((new_w, new_h), Image.BICUBIC)
        buf = BytesIO()
        image.save(buf, format="PNG")
        template_png = buf.getvalue()
        template_w, template_h = new_w, new_h
    return template_png, template_w, template_h


async def run_master_key_approval_pipeline(
    *,
    assignment_id: str,
    user_id: str,
    payload: bytes,
    template_original_name: str | None,
    debug_hook: Callable[[bytes, str], None] | None = None,
) -> MasterKeyApprovalResult:
    assignment = get_assignment(
        assignment_id,
        user_id,
        columns="id,owner_id,template_version,template_storage_path,template_regions_json",
    )
    owner_id = str(assignment.get("owner_id") or user_id or "unknown")
    had_template = bool(assignment.get("template_storage_path") and assignment.get("template_regions_json"))
    template_png, template_w, template_h = _normalize_template_image(payload)

    if debug_hook is not None:
        try:
            debug_hook(template_png, assignment_id)
        except Exception as exc:
            logger.warning("template_debug_hook_failed assignment_id=%s error=%s", assignment_id, exc)

    template_key = f"{owner_id}/templates/{assignment_id}.png"
    upload_bytes(SUBMISSIONS_BUCKET, template_key, template_png, "image/png")
    template_storage_path = f"{SUBMISSIONS_BUCKET}/{template_key}"

    try:
        raw = await ocr_service.extract_text(image_bytes=template_png)
        norm = normalize_ocr_result(raw)
        regions, warnings = build_anchor_template_regions(
            ocr_boxes=norm.get("boxes"),
            image_size=(template_w, template_h),
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    try:
        template_version = int(assignment.get("template_version") or 0) + 1
    except Exception:
        template_version = 1
    template_upload_id = str(uuid4())
    template_uploaded_at = _utc_iso()
    template_regions_raw = build_template_regions_payload(regions, (template_w, template_h))
    template_regions = with_approved_manifest(
        template_regions_raw,
        template_version=template_version,
        template_width_px=template_w,
        template_height_px=template_h,
        approved_at=template_uploaded_at,
    )

    sb = require_supabase()
    sb.table("assignments").update(
        {
            "template_storage_path": template_storage_path,
            "template_width_px": template_w,
            "template_height_px": template_h,
            "template_regions_json": template_regions,
            "template_version": template_version,
            "template_upload_id": template_upload_id,
            "template_original_name": template_original_name,
            "template_uploaded_at": template_uploaded_at,
        }
    ).eq("id", assignment_id).execute()

    logger.info(
        "template_regions_saved assignment_id=%s regions=%s template_w=%s template_h=%s",
        assignment_id,
        len(template_regions.get("regions") or []),
        template_w,
        template_h,
    )

    if not template_regions.get("regions"):
        logger.warning("template_regions_empty assignment_id=%s", assignment_id)
        try:
            sb.table("assignments").update({"needs_review": True}).eq("id", assignment_id).execute()
        except Exception as exc:
            logger.warning("template_regions_empty_needs_review_failed assignment_id=%s error=%s", assignment_id, exc)

    if had_template:
        sb.table("uploads").update({"needs_review": True}).eq("assignment_id", assignment_id).eq(
            "owner_id", user_id
        ).execute()

    qids = [str(r.get("qid")) for r in (template_regions.get("regions") or []) if isinstance(r, dict)]
    manifest = template_regions.get("manifest") if isinstance(template_regions, dict) else {}
    boxes_detected = len((manifest or {}).get("questions") or [])
    return MasterKeyApprovalResult(
        assignment_id=assignment_id,
        template_storage_path=template_storage_path,
        template_version=template_version,
        template_upload_id=template_upload_id,
        template_original_name=template_original_name,
        template_uploaded_at=template_uploaded_at,
        boxes_detected=boxes_detected,
        qids=qids,
        warnings=[str(w) for w in (warnings or [])],
    )
