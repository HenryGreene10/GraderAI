import datetime as dt
import logging
import os
from io import BytesIO
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..auth import get_current_user_id
from ..config import (
    GRADED_BUCKET,
    OCR_REVIEW_THRESHOLD,
    OVERLAYS_BUCKET,
)
from ..models.schemas import GradeResult, Overlay
from ..services import ocr as ocr_service
from ..services.db import get_upload, update_upload
from ..services.grader import (
    RUBRIC_VERSION,
    PROMPT_VERSION,
    build_overlay_for_result,
    generate_autokeys,
    grade,
    parse_questions,
)
from ..services.ocr import normalize_ocr_result
from ..services.report import flatten_to_pdf
from ..services.scan_pipeline import prepare_ocr_image
from ..services.storage import download_submission_bytes, upload_bytes, upload_json
from PIL import Image

logger = logging.getLogger(__name__)

router = APIRouter(tags=["grade"])


class StartGradeBody(BaseModel):
    upload_id: str


def _utc_iso() -> str:
    return dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _log_ocr_image_size(tag: str, image_bytes: bytes) -> None:
    try:
        with Image.open(BytesIO(image_bytes)) as img:
            logger.info("%s OCR input size: %sx%s px", tag, img.width, img.height)
    except Exception:
        return


def _needs_review(result: GradeResult, ocr_confidence: Optional[float], text: str) -> bool:
    if result.needs_review:
        return True
    if ocr_confidence is not None and ocr_confidence < OCR_REVIEW_THRESHOLD:
        return True
    if len(text.strip()) < 12:
        return True
    return False


async def _ensure_ocr(row: dict) -> dict:
    text = (row.get("ocr_text") or row.get("extracted_text") or "").strip()
    if text:
        return {
            "text": text,
            "boxes": row.get("ocr_boxes"),
            "confidence": row.get("ocr_confidence"),
        }

    storage_path = row.get("storage_path")
    if not storage_path:
        raise HTTPException(status_code=400, detail="Missing storage_path")

    blob = download_submission_bytes(storage_path)
    scan_artifacts = None
    scan_error = None
    ocr_bytes = blob
    try:
        ocr_bytes, scan_artifacts = prepare_ocr_image(
            blob,
            row.get("mime_type"),
            row.get("owner_id") or "unknown",
            row["id"],
        )
    except Exception as exc:
        scan_error = str(exc)
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

    _log_ocr_image_size("_ensure_ocr", ocr_bytes)
    try:
        raw = await ocr_service.extract_text(image_bytes=ocr_bytes)
    except Exception as exc:
        update_upload(
            row["id"],
            {
                "ocr_status": "error",
                "status": "error",
                "ocr_error": str(exc),
                "updated_at": _utc_iso(),
            },
        )
        raise HTTPException(status_code=500, detail=f"OCR failed: {exc}")
    norm = normalize_ocr_result(raw)
    text = (norm.get("text") or "").strip()
    if not text:
        update_upload(
            row["id"],
            {
                "ocr_status": "error",
                "status": "error",
                "ocr_error": "OCR returned empty text",
                "updated_at": _utc_iso(),
            },
        )
        raise HTTPException(status_code=422, detail="OCR returned empty text")

    scan_failed = bool(scan_artifacts and not scan_artifacts.scan_ok) or bool(scan_error)
    needs_review = bool(row.get("needs_review")) or scan_failed
    update_upload(
        row["id"],
        {
            "ocr_status": "done",
            "status": "ocr_done",
            "ocr_text": text,
            "extracted_text": text,
            "ocr_boxes": norm.get("boxes"),
            "ocr_confidence": norm.get("confidence"),
            "ocr_error": None,
            "normalized_image_path": scan_artifacts.normalized_image_path if scan_artifacts else None,
            "normalized_pdf_path": scan_artifacts.normalized_pdf_path if scan_artifacts else None,
            "normalized_width_px": scan_artifacts.width_px if scan_artifacts else None,
            "normalized_height_px": scan_artifacts.height_px if scan_artifacts else None,
            "scan_status": "normalized" if (scan_artifacts and scan_artifacts.scan_ok) else ("fallback" if scan_failed else None),
            "scan_error": (scan_artifacts.error if scan_artifacts else scan_error),
            "needs_review": needs_review,
            "updated_at": _utc_iso(),
        },
    )

    return {"text": text, "boxes": norm.get("boxes"), "confidence": norm.get("confidence")}


@router.post("/api/grade")
async def start_grade(
    body: StartGradeBody,
    user_id: str = Depends(get_current_user_id),
):
    caller_id = user_id
    row = get_upload(
        body.upload_id,
        caller_id,
        columns=(
            "id,owner_id,storage_path,ocr_text,extracted_text,ocr_boxes,"
            "ocr_confidence,mime_type,needs_review,normalized_image_path"
        ),
    )

    ocr_result = await _ensure_ocr(row)
    text = ocr_result["text"]

    questions = parse_questions(text)
    keys = generate_autokeys(questions)
    result: GradeResult = grade(questions, keys, text)
    result.submission_id = row["id"]
    result.rubric_version = RUBRIC_VERSION
    result.prompt_version = PROMPT_VERSION

    overlay: Overlay = build_overlay_for_result(result)
    summary = (
        f"Submission: {row['id']}\n"
        f"Total: {result.total_score}/{result.total_max}\n"
        f"Rubric v{result.rubric_version} | Prompt v{result.prompt_version}\n"
        f"Needs review: {result.needs_review}"
    )
    pdf_bytes = flatten_to_pdf(summary, overlay)

    owner_id = row.get("owner_id") or caller_id or "unknown"
    overlay_key = f"{owner_id}/{row['id']}.json"
    pdf_key = f"{owner_id}/{row['id']}.pdf"

    try:
        upload_json(OVERLAYS_BUCKET, overlay_key, overlay.model_dump())
    except Exception:
        # Best-effort overlay upload
        pass

    try:
        upload_bytes(GRADED_BUCKET, pdf_key, pdf_bytes, "application/pdf")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"PDF upload failed: {exc}")

    needs_review = _needs_review(result, ocr_result.get("confidence"), text) or bool(row.get("needs_review"))

    update_upload(
        row["id"],
        {
            "status": "graded",
            "needs_review": needs_review,
            "graded_pdf_path": pdf_key,
            "overlay_path": overlay_key,
            "overlay_json": overlay.model_dump(),
            "grade_json": result.model_dump(),
            "rubric_version": result.rubric_version,
            "prompt_version": result.prompt_version,
            "updated_at": _utc_iso(),
        },
    )

    return {
        "ok": True,
        "upload_id": row["id"],
        "needs_review": needs_review,
        "graded_pdf_path": pdf_key,
        "overlay_path": overlay_key,
        "grade": result.model_dump(),
    }
