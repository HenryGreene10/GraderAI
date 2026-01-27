import datetime as dt
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..auth import get_current_user_id
from ..services import ocr
from ..services.db import get_upload, update_upload
from ..services.ocr import normalize_ocr_result
from ..services.storage import download_submission_bytes

router = APIRouter(prefix="/api/ocr", tags=["ocr"])
logger = logging.getLogger(__name__)

OCR_PENDING = "pending"
OCR_DONE = "done"
OCR_ERROR = "error"


class StartOCRBody(BaseModel):
    upload_id: str


def _utc_iso() -> str:
    return dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


async def run_ocr_for_upload(upload_id: str, user_id: str) -> dict:
    row = get_upload(upload_id, user_id, columns="id,owner_id,storage_path")
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
        raw = await ocr.extract_text(image_bytes=blob)
        norm = normalize_ocr_result(raw)
        text = (norm.get("text") or "").strip()
        if not text:
            raise HTTPException(status_code=422, detail="OCR returned empty text")

        payload = {
            "ocr_status": OCR_DONE,
            "status": "ocr_done",
            "ocr_text": text,
            "extracted_text": text,
            "ocr_boxes": norm.get("boxes"),
            "ocr_confidence": norm.get("confidence"),
            "ocr_error": None,
            "updated_at": _utc_iso(),
        }
        update_upload(row["id"], payload)
        try:
            from .uploads import run_grade_pipeline

            await run_grade_pipeline(row["id"], user_id)
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
):
    return await run_ocr_for_upload(body.upload_id, user_id)


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
