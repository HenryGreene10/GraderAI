from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO
from typing import Callable
from uuid import uuid4

from fastapi import HTTPException
from PIL import Image
from PIL import ImageDraw

from ..config import SUBMISSIONS_BUCKET
from . import ocr as ocr_service
from .db import get_assignment, require_supabase
from .ocr import normalize_ocr_result
from .scanner import MAX_DIM_PX, normalize_image_bytes
from .storage import upload_bytes
from .template_anchor_regions import build_anchor_template_regions
from .template_manifest import manifest_from_template_regions, manifest_to_template_regions_payload, with_approved_manifest
from .template_regions import build_template_regions_payload
from .template import detect_answer_boxes

logger = logging.getLogger(__name__)

TEMPLATE_MIN_LONG_EDGE = 1200
_Q_LABELISH_RE = re.compile(r"^\s*[\(\[]?\s*Q\s*\d{0,2}\s*[\)\].,:;]?\s*$", re.I)
_DIV_EXPR_RE = re.compile(r"^\s*\d+\)\d+\s*$")
_ONLY_PUNCT_RE = re.compile(r"^\s*[\W_]+\s*$")
_BLOCKING_WARNING_CODES = {
    "BOX_COUNT_TOO_FEW",
    "BOX_COUNT_TOO_MANY",
    "BOX_OVERLAP_AMBIGUOUS",
    "BOX_CONFIDENCE_LOW",
    "BOX_DUPLICATE_Q_NUMBERS",
    "BOX_MISSING_Q_NUMBERS",
    "BOX_ROW_FALLBACK_QIDS",
    "BOX_UNREADABLE_Q_LABEL_ROWS",
    "BOX_MODE_FALLBACK_APPLIED",
    "ANCHOR_AMBIGUITY_HIGH",
    "ANCHOR_HARD_GATE_RELAXED",
    "ANCHOR_DUPLICATE_ANSWER_BOXES",
    "ANCHOR_EXPLICIT_MISSING_FROM_MANIFEST",
    "ANCHOR_COVERAGE_BELOW_THRESHOLD",
    "EXPECTED_TEXT_QUALITY_LOW",
}


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
    warnings: list[dict[str, object]]
    anchor_trace: dict[str, object] | None
    approval_blocked: bool
    approval_warning: str | None


def _utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _is_low_quality_expected_text(text: str) -> bool:
    s = str(text or "").strip()
    if not s:
        return True
    if _ONLY_PUNCT_RE.match(s):
        return True
    if _Q_LABELISH_RE.match(s):
        return True
    if _DIV_EXPR_RE.match(s):
        return True
    return False


def _append_expected_text_quality_warning(
    template_regions_payload: dict[str, object],
    warnings: list[dict[str, object]],
) -> None:
    regions = template_regions_payload.get("regions") if isinstance(template_regions_payload, dict) else None
    if not isinstance(regions, list):
        return
    bad_qids: list[str] = []
    for item in regions:
        if not isinstance(item, dict):
            continue
        qid = str(item.get("qid") or "").strip()
        expected = str(item.get("expected_answer_text") or "")
        if qid and _is_low_quality_expected_text(expected):
            bad_qids.append(qid)
    if not bad_qids:
        return
    warnings.append(
        {
            "code": "EXPECTED_TEXT_QUALITY_LOW",
            "count": len(bad_qids),
            "qids": bad_qids,
            "message": "One or more expected answer texts look invalid (empty, label-like, punctuation, or division-expression).",
        }
    )


def _blocking_reasons_from_warning_codes(warning_codes: set[str]) -> list[str]:
    return sorted(code for code in warning_codes if code in _BLOCKING_WARNING_CODES)


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


def _save_anchor_debug_overlay(
    *,
    template_png: bytes,
    assignment_id: str,
    anchor_trace: dict[str, object],
) -> str:
    rows = anchor_trace.get("rows") if isinstance(anchor_trace, dict) else None
    if not isinstance(rows, list) or not rows:
        return ""

    image = Image.open(BytesIO(template_png)).convert("RGB")
    draw = ImageDraw.Draw(image)

    for item in rows:
        if not isinstance(item, dict):
            continue
        box = item.get("box_bbox_px")
        roi = item.get("roi_bbox_px")
        anchor = item.get("anchor_bbox_px")
        parsed_num = item.get("parsed_num")

        if isinstance(box, list) and len(box) >= 4:
            bx, by, bw, bh = float(box[0]), float(box[1]), float(box[2]), float(box[3])
            draw.rectangle([bx, by, bx + bw, by + bh], outline=(0, 170, 0), width=3)
        if isinstance(roi, list) and len(roi) >= 4:
            rx, ry, rw, rh = float(roi[0]), float(roi[1]), float(roi[2]), float(roi[3])
            draw.rectangle([rx, ry, rx + rw, ry + rh], outline=(0, 102, 255), width=2)
        if isinstance(anchor, list) and len(anchor) >= 4:
            ax, ay, aw, ah = float(anchor[0]), float(anchor[1]), float(anchor[2]), float(anchor[3])
            draw.rectangle([ax, ay, ax + aw, ay + ah], outline=(220, 0, 0), width=3)
            label = f"Q{parsed_num}" if isinstance(parsed_num, int) and parsed_num > 0 else "Q?"
            draw.text((ax + 3, max(0.0, ay - 14)), label, fill=(220, 0, 0))

    tmp_dir = os.path.join(os.path.dirname(__file__), "..", "tmp")
    os.makedirs(tmp_dir, exist_ok=True)
    filename = f"template_anchor_overlay_{assignment_id}_{int(datetime.now(timezone.utc).timestamp())}.png"
    path = os.path.join(tmp_dir, filename)
    image.save(path, format="PNG")
    return path


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
        answer_box_hints: list[tuple[float, float, float, float]] = []
        try:
            answer_box_hints = [
                (float(b[0]), float(b[1]), float(b[2]), float(b[3]))
                for b in (detect_answer_boxes(template_png) or [])
                if isinstance(b, (list, tuple)) and len(b) >= 4
            ]
        except Exception as exc:
            logger.warning("template_answer_box_detection_failed assignment_id=%s error=%s", assignment_id, exc)
        raw = await ocr_service.extract_text(image_bytes=template_png)
        norm = normalize_ocr_result(raw)
        regions, warnings, anchor_trace = build_anchor_template_regions(
            ocr_boxes=norm.get("boxes"),
            image_size=(template_w, template_h),
            answer_box_hints=answer_box_hints,
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
    _append_expected_text_quality_warning(template_regions_raw, warnings)
    warning_codes = {
        str(item.get("code"))
        for item in (warnings or [])
        if isinstance(item, dict) and item.get("code")
    }
    blocking_hits = _blocking_reasons_from_warning_codes(warning_codes)
    approval_blocked = bool(blocking_hits)
    if approval_blocked:
        manifest = manifest_from_template_regions(
            template_regions_raw,
            template_version=template_version,
            template_width_px=template_w,
            template_height_px=template_h,
        )
        template_regions = manifest_to_template_regions_payload(manifest)
        template_regions["manifest_approval_blocked"] = True
        template_regions["manifest_approval_block_reasons"] = blocking_hits
    else:
        template_regions = with_approved_manifest(
            template_regions_raw,
            template_version=template_version,
            template_width_px=template_w,
            template_height_px=template_h,
            approved_at=template_uploaded_at,
        )
    if isinstance(anchor_trace, dict):
        try:
            overlay_path = _save_anchor_debug_overlay(
                template_png=template_png,
                assignment_id=assignment_id,
                anchor_trace=anchor_trace,
            )
            if overlay_path:
                anchor_trace["debug_overlay_path"] = overlay_path
        except Exception as exc:
            logger.warning("template_anchor_overlay_failed assignment_id=%s error=%s", assignment_id, exc)
        template_regions["anchor_trace"] = anchor_trace
    if warnings:
        template_regions["warnings"] = [w for w in warnings if isinstance(w, dict)]

    anchor_ambiguity_high = "ANCHOR_AMBIGUITY_HIGH" in warning_codes
    needs_review_for_template = anchor_ambiguity_high or approval_blocked
    approval_warning = None
    if approval_blocked:
        approval_warning = (
            "Master key needs review before approval: "
            + ", ".join(blocking_hits)
            + "."
        )
        warnings.append(
            {
                "code": "TEMPLATE_APPROVAL_BLOCKED",
                "message": approval_warning,
                "reasons": blocking_hits,
            }
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
            "needs_review": needs_review_for_template,
        }
    ).eq("id", assignment_id).execute()
    if anchor_ambiguity_high:
        logger.warning("template_anchor_ambiguity assignment_id=%s codes=%s", assignment_id, sorted(warning_codes))
    if approval_blocked:
        logger.warning("template_approval_blocked assignment_id=%s reasons=%s", assignment_id, blocking_hits)

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
        warnings=[w for w in (warnings or []) if isinstance(w, dict)],
        anchor_trace=anchor_trace if isinstance(anchor_trace, dict) else None,
        approval_blocked=approval_blocked,
        approval_warning=approval_warning,
    )
