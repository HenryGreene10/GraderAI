import datetime as dt
from typing import Optional

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from ..services import ocr
from ..services.db import get_upload, update_upload
from ..services.ocr import normalize_ocr_result
from ..services.storage import download_submission_bytes

router = APIRouter(prefix="/api/ocr", tags=["ocr"])

OCR_PENDING = "pending"
OCR_PROCESSING = "processing"
OCR_DONE = "done"
OCR_FAILED = "failed"


class StartOCRBody(BaseModel):
    upload_id: str


def _utc_iso() -> str:
    return dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


@router.post("/start")
async def start_ocr(
    body: StartOCRBody,
    x_owner_id: Optional[str] = Header(None),
    x_user_id: Optional[str] = Header(None),
):
    caller_id = x_owner_id or x_user_id
    row = get_upload(body.upload_id, caller_id, columns="id,owner_id,storage_path")
    storage_path = row.get("storage_path")
    if not storage_path:
        raise HTTPException(status_code=400, detail="Missing storage_path")

    update_upload(
        row["id"],
        {
            "ocr_status": OCR_PROCESSING,
            "status": OCR_PROCESSING,
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
            "status": OCR_DONE,
            "ocr_text": text,
            "extracted_text": text,
            "ocr_boxes": norm.get("boxes"),
            "ocr_confidence": norm.get("confidence"),
            "ocr_error": None,
            "updated_at": _utc_iso(),
        }
        update_upload(row["id"], payload)
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
                "ocr_status": OCR_FAILED,
                "status": OCR_FAILED,
                "ocr_error": str(exc),
                "updated_at": _utc_iso(),
            },
        )
        raise HTTPException(status_code=500, detail=f"OCR failed: {exc}")


@router.get("/status/{upload_id}")
def ocr_status(
    upload_id: str,
    x_owner_id: Optional[str] = Header(None),
    x_user_id: Optional[str] = Header(None),
):
    caller_id = x_owner_id or x_user_id
    row = get_upload(
        upload_id,
        caller_id,
        columns="id,owner_id,ocr_status,ocr_error,ocr_text,extracted_text",
    )
    text = (row.get("ocr_text") or row.get("extracted_text") or "").strip()
    status = (row.get("ocr_status") or "").strip().lower()
    if row.get("ocr_error"):
        status = OCR_FAILED
    elif status:
        status = status
    elif text:
        status = OCR_DONE
    else:
        status = OCR_PENDING

    payload = {"status": status, "text_len": len(text) if text else 0}
    if status == OCR_FAILED and row.get("ocr_error"):
        payload["error"] = row.get("ocr_error")
    return payload
