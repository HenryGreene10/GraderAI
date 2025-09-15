import os, json, asyncio
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
import httpx

from supabase import create_client, Client

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_ROLE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
SUPABASE_BUCKET = os.environ.get("SUPABASE_BUCKET", "submissions")
HANDWRITINGOCR_API_KEY = os.environ["HANDWRITINGOCR_API_KEY"]
OWNER_COLUMN = os.environ.get("OWNER_COLUMN", "user_id")  # set to your column name if different
MAX_RETRIES = int(os.environ.get("OCR_MAX_RETRIES", "3"))

supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)

app = FastAPI()
# Allow your local dev and your deployed frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get("CORS_ALLOW_ORIGINS", "http://localhost:5173").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/health")
def health():
    return {"ok": True, "service": "graderai-ocr"}

# ---------- Helpers ----------
def _owner_matches(row: dict, user_id: str):
    val = row.get(OWNER_COLUMN)
    return bool(user_id and val and val == user_id)

def _mark_status(upload_id: str, status: str, fields: dict | None = None):
    payload = {"status": status}
    if fields:
        payload.update(fields)
    supabase.table("uploads").update(payload).eq("id", upload_id).execute()

def _get_signed_url(path: str, expires_in: int = 900):
    resp = supabase.storage.from_(SUPABASE_BUCKET).create_signed_url(path, expires_in)
    url = resp.get("signedURL") or resp.get("signed_url") or resp
    if not url:
        raise HTTPException(500, "Could not sign storage URL")
    return url if str(url).startswith("http") else f"{SUPABASE_URL}/storage/v1/object/sign/{SUPABASE_BUCKET}/{path}?{url}"

async def _download_bytes(url: str) -> bytes:
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.get(url)
        r.raise_for_status()
        return r.content

async def _call_handwritingocr(image_bytes: bytes) -> dict:
    endpoint = "https://www.handwritingocr.com/api/v3/ocr"  # adjust if needed
    headers = {"x-api-key": HANDWRITINGOCR_API_KEY}
    files = {"file": ("upload", image_bytes)}
    async with httpx.AsyncClient(timeout=120) as client:
        r = await client.post(endpoint, headers=headers, files=files)
        r.raise_for_status()
        return r.json()

def _parse_text(api_json: dict) -> tuple[str, dict]:
    text = ""
    if isinstance(api_json, dict):
        if isinstance(api_json.get("text"), str):
            text = api_json["text"]
        elif isinstance(api_json.get("pages"), list):
            text = "\n".join(p.get("text", "") for p in api_json["pages"])
    return text.strip(), api_json

# ---------- API ----------
from pydantic import BaseModel
class StartOCRBody(BaseModel):
    upload_id: str

@app.post("/api/ocr/start")
async def start_ocr(body: StartOCRBody, x_user_id: str = Header(None)):
    # 1) Read upload row
    resp = supabase.table("uploads").select("*").eq("id", body.upload_id).single().execute()
    row = resp.data
    if not row:
        raise HTTPException(404, "Upload not found")
    if not _owner_matches(row, x_user_id):
        raise HTTPException(403, "Forbidden")

    storage_path = row.get("storage_path")
    if not storage_path:
        raise HTTPException(400, "Missing storage_path for upload")

    _mark_status(row["id"], "processing",
                 {"ocr_started_at": datetime.now(timezone.utc).isoformat(), "ocr_error": None})

    try:
        signed = _get_signed_url(storage_path)
        blob = await _download_bytes(signed)

        last_err = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                ocr_json = await _call_handwritingocr(blob)
                text, meta = _parse_text(ocr_json)
                if not text:
                    raise ValueError("OCR returned empty text")

                _mark_status(row["id"], "done", {
                    "extracted_text": text,
                    "ocr_completed_at": datetime.now(timezone.utc).isoformat(),
                    "ocr_meta": json.dumps(meta)
                })
                return {"ok": True, "status": "done", "upload_id": row["id"]}
            except Exception as e:
                last_err = str(e)
                await asyncio.sleep(1.5 * attempt)

        _mark_status(row["id"], "failed", {"ocr_error": last_err})
        raise HTTPException(502, f"OCR failed after retries: {last_err}")

    except HTTPException:
        raise
    except Exception as e:
        _mark_status(row["id"], "failed", {"ocr_error": str(e)})
        raise HTTPException(500, f"OCR pipeline error: {e}")

@app.get("/api/ocr/status/{upload_id}")
async def ocr_status(upload_id: str, x_user_id: str = Header(None)):
    resp = supabase.table("uploads").select("*").eq("id", upload_id).single().execute()
    row = resp.data
    if not row:
        raise HTTPException(404, "Upload not found")
    if not _owner_matches(row, x_user_id):
        raise HTTPException(403, "Forbidden")
    return {
        "status": row.get("status", "pending"),
        "extracted_text": row.get("extracted_text"),
        "error": row.get("ocr_error"),
        "started": row.get("ocr_started_at"),
        "completed": row.get("ocr_completed_at"),
    }
