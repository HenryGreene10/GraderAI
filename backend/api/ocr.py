import datetime as dt
import logging
import os
from io import BytesIO

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from ..auth import get_current_user_id
from ..services import ocr
from ..services.db import get_upload, update_upload
from ..services.ocr import infer_primary_page_size_px, normalize_ocr_result
from ..services.scan_pipeline import prepare_ocr_image
from ..services.storage import download_submission_bytes
from ..services.debug_artifacts import (
    debug_enabled,
    draw_ocr_overlay,
    log_debug,
    serialize_json,
    to_png_bytes,
    upload_debug_artifact,
)
from PIL import Image

router = APIRouter(prefix="/api/ocr", tags=["ocr"])
logger = logging.getLogger(__name__)

OCR_PENDING = "pending"
OCR_DONE = "done"
OCR_ERROR = "error"


class StartOCRBody(BaseModel):
    upload_id: str


def _utc_iso() -> str:
    return dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _log_ocr_image_size(tag: str, image_bytes: bytes) -> None:
    try:
        with Image.open(BytesIO(image_bytes)) as img:
            logger.info("%s OCR input size: %sx%s px", tag, img.width, img.height)
    except Exception:
        return


def _canonical_upload_size(row: dict, inferred_w: float, inferred_h: float) -> tuple[float, float]:
    try:
        w = float(row.get("normalized_width_px") or 0.0)
        h = float(row.get("normalized_height_px") or 0.0)
    except Exception:
        w, h = 0.0, 0.0
    if w > 0 and h > 0:
        return w, h
    page_sizes = row.get("page_sizes_json")
    if isinstance(page_sizes, list) and page_sizes:
        first = page_sizes[0] if isinstance(page_sizes[0], dict) else {}
        try:
            w = float(first.get("width_px") or 0.0)
            h = float(first.get("height_px") or 0.0)
        except Exception:
            w, h = 0.0, 0.0
        if w > 0 and h > 0:
            return w, h
    if inferred_w > 0 and inferred_h > 0:
        return inferred_w, inferred_h
    return 0.0, 0.0


async def run_ocr_for_upload(
    upload_id: str,
    user_id: str,
    *,
    debug: bool = False,
    pipeline_source: str = "ocr.auto",
) -> dict:
    row = get_upload(
        upload_id,
        user_id,
        columns=(
            "id,owner_id,storage_path,mime_type,needs_review,normalized_image_path,"
            "normalized_width_px,normalized_height_px,page_sizes_json"
        ),
    )
    storage_path = row.get("storage_path")
    if not storage_path:
        raise HTTPException(status_code=400, detail="Missing storage_path")

    update_upload(
        row["id"],
        {
            "ocr_status": OCR_PENDING,
            "status": "ocr_running",
            "ocr_error": None,
            "updated_at": _utc_iso(),
        },
    )

    try:
        blob = download_submission_bytes(storage_path)
        scan_artifacts = None
        scan_error = None
        ocr_bytes = blob
        try:
            ocr_bytes, scan_artifacts = prepare_ocr_image(
                blob,
                row.get("mime_type"),
                row.get("owner_id") or user_id or "unknown",
                row["id"],
            )
        except Exception as exc:
            scan_artifacts = None
            scan_error = str(exc)
            logger.warning("Scan normalization failed for %s: %s", row["id"], exc)
            ocr_bytes = blob

        provider = os.getenv("OCR_PROVIDER", "").strip().lower()
        if provider == "azure":
            if scan_artifacts:
                ocr_bytes = scan_artifacts.normalized_image_bytes
            else:
                normalized_path = row.get("normalized_image_path")
                if normalized_path:
                    try:
                        ocr_bytes = download_submission_bytes(normalized_path)
                    except Exception:
                        ocr_bytes = blob

        _log_ocr_image_size("run_ocr_for_upload", ocr_bytes)
        raw = await ocr.extract_text(image_bytes=ocr_bytes)
        norm = normalize_ocr_result(raw)
        text = (norm.get("text") or "").strip()
        if not text:
            update_upload(
                row["id"],
                {
                    "ocr_status": OCR_ERROR,
                    "status": "error",
                    "ocr_error": "OCR returned empty text",
                    "updated_at": _utc_iso(),
                },
            )
            raise HTTPException(status_code=422, detail="OCR returned empty text")

        scan_failed = bool(scan_artifacts and not scan_artifacts.scan_ok) or bool(scan_error)
        needs_review = bool(row.get("needs_review")) or scan_failed
        frame_w, frame_h = infer_primary_page_size_px(norm.get("boxes"))
        canonical_w, canonical_h = _canonical_upload_size(row, frame_w, frame_h)
        payload = {
            "ocr_status": OCR_DONE,
            "status": "ocr_done",
            "ocr_text": text,
            "extracted_text": text,
            "ocr_boxes": norm.get("boxes"),
            "ocr_confidence": norm.get("confidence"),
            "ocr_error": None,
            "updated_at": _utc_iso(),
            "normalized_width_px": int(round(canonical_w)) if canonical_w > 0 else None,
            "normalized_height_px": int(round(canonical_h)) if canonical_h > 0 else None,
        }
        if scan_artifacts:
            payload.update(
                {
                    "normalized_image_path": scan_artifacts.normalized_image_path,
                    "normalized_pdf_path": scan_artifacts.normalized_pdf_path,
                    "normalized_width_px": scan_artifacts.width_px,
                    "normalized_height_px": scan_artifacts.height_px,
                    "scan_status": "normalized" if scan_artifacts.scan_ok else "fallback",
                    "scan_error": scan_artifacts.error,
                    "needs_review": needs_review,
                }
            )
        elif scan_error:
            payload.update(
                {
                    "scan_status": "fallback",
                    "scan_error": scan_error,
                    "needs_review": needs_review,
                }
            )
        update_upload(row["id"], payload)

        if debug_enabled(debug):
            owner_id = row.get("owner_id") or user_id or "unknown"
            debug_fields = {
                "normalized_w_px": scan_artifacts.width_px if scan_artifacts else None,
                "normalized_h_px": scan_artifacts.height_px if scan_artifacts else None,
            }
            log_debug("ocr", debug_fields)
            try:
                upload_debug_artifact(
                    owner_id,
                    row["id"],
                    "normalized.png",
                    to_png_bytes(ocr_bytes),
                    "image/png",
                )
                upload_debug_artifact(
                    owner_id,
                    row["id"],
                    "ocr_raw.json",
                    serialize_json(raw),
                    "application/json",
                )
                overlay_png, counts = draw_ocr_overlay(ocr_bytes, norm.get("boxes") or {})
                upload_debug_artifact(
                    owner_id,
                    row["id"],
                    "ocr_overlay.png",
                    overlay_png,
                    "image/png",
                )
                log_debug("ocr_boxes", counts)
            except Exception:
                logger.exception("Failed to create OCR debug bundle for %s", row["id"])
        try:
            from .uploads import run_grade_pipeline

            await run_grade_pipeline(row["id"], user_id, debug=debug, source=pipeline_source)
        except HTTPException as exc:
            logger.warning("Auto-grade failed for %s: %s", row["id"], exc.detail)
        except Exception:
            logger.exception("Auto-grade failed for %s", row["id"])
        return {
            "ok": True,
            "status": OCR_DONE,
            "ocr_status": OCR_DONE,
            "upload_id": row["id"],
            "text_len": len(text),
        }
    except HTTPException:
        raise
    except Exception as exc:
        update_upload(
            row["id"],
            {
                "ocr_status": OCR_ERROR,
                "status": "error",
                "ocr_error": str(exc),
                "updated_at": _utc_iso(),
            },
        )
        raise HTTPException(status_code=500, detail=f"OCR failed: {exc}")


@router.post("/start")
async def start_ocr(
    body: StartOCRBody,
    user_id: str = Depends(get_current_user_id),
    debug: bool = Query(False),
):
    return await run_ocr_for_upload(
        body.upload_id,
        user_id,
        debug=debug,
        pipeline_source="ocr.start",
    )


@router.get("/status/{upload_id}")
def ocr_status(
    upload_id: str,
    user_id: str = Depends(get_current_user_id),
):
    row = get_upload(
        upload_id,
        user_id,
        columns="id,owner_id,ocr_status,ocr_error,ocr_text,extracted_text",
    )
    text = (row.get("ocr_text") or row.get("extracted_text") or "").strip()
    status = (row.get("ocr_status") or "").strip().lower()
    if row.get("ocr_error"):
        status = OCR_ERROR
    elif status:
        status = status
    elif text:
        status = OCR_DONE
    else:
        status = OCR_PENDING

    payload = {"status": status, "text_len": len(text) if text else 0}
    if status == OCR_ERROR and row.get("ocr_error"):
        payload["error"] = row.get("ocr_error")
    return payload
