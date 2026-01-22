import datetime as dt
from typing import Optional

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from ..config import (
    GRADED_BUCKET,
    OCR_REVIEW_THRESHOLD,
    OVERLAYS_BUCKET,
    REQUIRE_OWNER,
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
from ..services.storage import download_submission_bytes, upload_bytes, upload_json

router = APIRouter(tags=["grade"])


class StartGradeBody(BaseModel):
    upload_id: str


def _utc_iso() -> str:
    return dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


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
    raw = await ocr_service.extract_text(image_bytes=blob)
    norm = normalize_ocr_result(raw)
    text = (norm.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=422, detail="OCR returned empty text")

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
            "updated_at": _utc_iso(),
        },
    )

    return {"text": text, "boxes": norm.get("boxes"), "confidence": norm.get("confidence")}


@router.post("/api/grade")
async def start_grade(
    body: StartGradeBody,
    x_owner_id: Optional[str] = Header(None),
    x_user_id: Optional[str] = Header(None),
):
    caller_id = x_owner_id or x_user_id
    row = get_upload(
        body.upload_id,
        caller_id,
        columns="id,owner_id,storage_path,ocr_text,extracted_text,ocr_boxes,ocr_confidence",
    )
    if REQUIRE_OWNER and caller_id and str(row.get("owner_id")) != str(caller_id):
        raise HTTPException(status_code=403, detail="Forbidden")

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

    needs_review = _needs_review(result, ocr_result.get("confidence"), text)

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
