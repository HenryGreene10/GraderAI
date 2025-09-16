# backend/app.py
import os, json, asyncio, base64
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
import httpx
from pydantic import BaseModel
from supabase import create_client, Client

# ----- Config (Render env) -----
SUPABASE_URL = os.environ["SUPABASE_URL"]                       # https://<project-ref>.supabase.co
SUPABASE_SERVICE_ROLE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
SUPABASE_BUCKET = os.environ.get("SUPABASE_BUCKET", "submissions")
OWNER_COLUMN = os.environ.get("OWNER_COLUMN", "owner_id")

HANDWRITINGOCR_API_KEY = os.environ.get("HANDWRITINGOCR_API_KEY", "")
HANDWRITINGOCR_ENDPOINT = os.environ.get("HANDWRITINGOCR_ENDPOINT", "https://www.handwritingocr.com/api/v3/ocr")
HANDWRITINGOCR_FILE_FIELD = os.environ.get("HANDWRITINGOCR_FILE_FIELD", "file")
HANDWRITINGOCR_URL_FIELD  = os.environ.get("HANDWRITINGOCR_URL_FIELD",  "url")
HANDWRITINGOCR_B64_FIELD  = os.environ.get("HANDWRITINGOCR_B64_FIELD",  "image_base64")
MAX_RETRIES = int(os.environ.get("OCR_MAX_RETRIES", "3"))

supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)

app = FastAPI()

# CORS: allow your frontend (or * when testing)
origins_env = os.environ.get("CORS_ALLOW_ORIGINS", "*")
origins = [o.strip() for o in origins_env.split(",") if o.strip()] or ["*"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

print("[boot] SUPABASE_URL =", SUPABASE_URL)

@app.get("/health")
def health():
    return {"ok": True, "service": "graderai-ocr"}

# ----- Helpers -----
def _owner_matches(row: dict, user_id: str):
    val = row.get(OWNER_COLUMN)
    return bool(user_id and val and val == user_id)

def _mark_status(upload_id: str, status: str, fields: dict | None = None):
    payload = {"status": status}
    if fields:
        payload.update(fields)
    supabase.table("uploads").update(payload).eq("id", upload_id).execute()

def _get_signed_url(path: str, expires_in: int = 900) -> str:
    # Normalize to bucket-relative key (no leading slash, no bucket prefix)
    key = (path or "").lstrip("/")
    if key.startswith(f"{SUPABASE_BUCKET}/"):
        key = key[len(f"{SUPABASE_BUCKET}/"):]
    print(f"[signing] project={SUPABASE_URL} bucket={SUPABASE_BUCKET} key={key}")

    resp = supabase.storage.from_(SUPABASE_BUCKET).create_signed_url(key, expires_in)

    signed = None
    if isinstance(resp, dict):
        signed = resp.get("signedURL") or resp.get("signed_url")
    if not signed:
        # Show a clear error in logs instead of generic 404 later
        raise HTTPException(404, f"Object not found for key={key}")

    # Some SDKs return a querystring; handle both
    return signed if signed.startswith("http") \
        else f"{SUPABASE_URL}/storage/v1/object/sign/{SUPABASE_BUCKET}/{key}?{signed}"

async def _download_bytes(url: str) -> bytes:
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.get(url)
        r.raise_for_status()
        return r.content

async def _call_handwritingocr(image_bytes: bytes, signed_url: str) -> dict:
    """Try multiple request shapes; log failures for debugging."""
    headers = {
        "x-api-key": HANDWRITINGOCR_API_KEY,
        "X-API-KEY": HANDWRITINGOCR_API_KEY,
    }
    attempts = []
    async with httpx.AsyncClient(timeout=120) as client:
        # (1) multipart file
        try:
            files = {HANDWRITINGOCR_FILE_FIELD: ("upload.jpg", image_bytes, "image/jpeg")}
            r = await client.post(HANDWRITINGOCR_ENDPOINT, headers=headers, files=files)
            if r.status_code == 200:
                return r.json()
            attempts.append(("multipart", r.status_code, r.text[:500]))
        except Exception as e:
            attempts.append(("multipart-exc", None, str(e)))
        # (2) JSON by URL
        try:
            r = await client.post(HANDWRITINGOCR_ENDPOINT, headers=headers, json={HANDWRITINGOCR_URL_FIELD: signed_url})
            if r.status_code == 200:
                return r.json()
            attempts.append(("json-url", r.status_code, r.text[:500]))
        except Exception as e:
            attempts.append(("json-url-exc", None, str(e)))
        # (3) JSON base64
        try:
            b64 = base64.b64encode(image_bytes).decode("ascii")
            r = await client.post(HANDWRITINGOCR_ENDPOINT, headers=headers, json={HANDWRITINGOCR_B64_FIELD: b64})
            if r.status_code == 200:
                return r.json()
            attempts.append(("json-b64", r.status_code, r.text[:500]))
        except Exception as e:
            attempts.append(("json-b64-exc", None, str(e)))

    print("OCR attempts:", json.dumps(attempts, ensure_ascii=False))
    raise RuntimeError(f"OCR provider rejected all shapes; first={attempts[0] if attempts else 'n/a'}")

def _parse_text(api_json: dict) -> tuple[str, dict]:
    text = ""
    if isinstance(api_json, dict):
        if isinstance(api_json.get("text"), str):
            text = api_json["text"]
        elif isinstance(api_json.get("pages"), list):
            text = "\n".join(p.get("text", "") for p in api_json["pages"])
    return text.strip(), api_json

# ----- API -----
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
        raise HTTPException(400, "Missing storage_path")

    # optimistic transition
    _mark_status(row["id"], "processing", {
        "ocr_started_at": datetime.now(timezone.utc).isoformat(),
        "ocr_error": None
    })

    # Uncomment ONLY if you ever need a quick fake end-to-end check:
    # if os.environ.get("FAKE_OCR") == "1":
    #     _mark_status(row["id"], "done", {"extracted_text": "Test OCR: detected sample text"})
    #     return {"ok": True, "status": "done", "upload_id": row["id"]}

    try:
        signed = _get_signed_url(storage_path)
        blob = await _download_bytes(signed)

        last_err = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                ocr_json = await _call_handwritingocr(blob, signed)
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
        raise HTTPException(502, f"OCR failed: {last_err}")

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
