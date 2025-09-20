# backend/app.py
import os, json, asyncio, base64, mimetypes, logging
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Header
from typing import Optional
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import Response
import httpx
from pydantic import BaseModel
from supabase import create_client, Client
from dotenv import load_dotenv
import logging
from uuid import UUID
# Supabase Python v2 raises PostgREST APIError
from postgrest.exceptions import APIError

# Simple app logger
logger = logging.getLogger("backend")
if not logger.handlers:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s"
    )

# New grading services
from .services.grader import (
    parse_questions,
    generate_autokeys,
    grade,
    build_overlay_for_result,
    RUBRIC_VERSION,
    PROMPT_VERSION,
)
from .services.report import flatten_to_pdf
from .services.ocr import extract_text as ocr_extract_text
from .models.schemas import GradeResult

# ── Environment ────────────────────────────────────────────────────────────────
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), ".env"))

# Now pull them in
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
SUPABASE_BUCKET = os.getenv("SUPABASE_BUCKET", "submissions")

print("DEBUG URL:", repr(SUPABASE_URL))
print("DEBUG KEY:", repr(SUPABASE_SERVICE_ROLE_KEY[:10] + "..."))

if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
    raise RuntimeError("Supabase credentials are missing. Check backend/.env")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
app = FastAPI()
allowed = os.environ.get("CORS_ALLOW_ORIGINS", "http://localhost:5173").split(",")
_CORS_ORIGINS = [o.strip() for o in allowed if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS", "PUT", "PATCH"],
    allow_headers=["*"],
    expose_headers=["*"],
)

logger.info("[boot] version=%s", "0.1.0")
logger.info("[boot] SUPABASE_URL=%s", SUPABASE_URL)
logger.info("[boot] CORS allow_origins=%s", _CORS_ORIGINS)
logger.info(
    "[boot] OCR_PROVIDER=%s hf_token_present=%s",
    (os.getenv("OCR_PROVIDER", "mock") or "mock"),
    bool(os.getenv("HF_TOKEN")),
)

# Warn if Supabase env incomplete; do not crash at startup
_missing_url = not bool(SUPABASE_URL)
_missing_key = not bool(SUPABASE_SERVICE_ROLE_KEY)
_missing_bucket = not bool(SUPABASE_BUCKET)
if _missing_url or _missing_key or _missing_bucket:
    logger.warning(
        "Supabase env missing: URL=%s, KEY=%s, BUCKET=%s",
        _missing_url,
        _missing_key,
        _missing_bucket,
    )

# ── Health ─────────────────────────────────────────────────────────────────────
@app.get("/health")
def health():
    return {"ok": True, "service": "graderai-ocr"}

@app.get("/healthz")
def healthz():
    return {"ok": True}

# Optional explicit OPTIONS (middleware already handles preflight; harmless to keep)
@app.options("/api/ocr/start")
async def ocr_start_options() -> Response:
    return Response(status_code=200)

@app.options("/api/ocr/status/{upload_id}")
async def ocr_status_options(upload_id: str) -> Response:
    return Response(status_code=200)

# ── Helpers ────────────────────────────────────────────────────────────────────
def _owner_matches(row: dict, caller_id: str | None) -> bool:
    if not caller_id or not row:
        return False
    return caller_id in {row.get("owner_id"), row.get("user_id")}

def _mark_status(upload_id: str, status: str, fields: dict | None = None):
    payload = {"status": status}
    if fields:
        payload.update(fields)
    supabase.table("uploads").update(payload).eq("id", upload_id).execute()

def _require_supabase_config():
    if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY or not SUPABASE_BUCKET:
        raise HTTPException(
            500,
            "Supabase configuration is incomplete. Please set SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, and SUPABASE_BUCKET.",
        )
        
        
def _is_uuid(val: str) -> bool:
    try:
        UUID(str(val))
        return True
    except Exception:
        return False
    
def _select_upload_row(supabase, upload_id: str):
    """
    Returns dict(row) or None. Converts Supabase 'maybe_single' responses safely.
    """
    resp = (
        supabase.table("uploads")
        .select("id, owner_id, storage_path, status, extracted_text")
        .eq("id", upload_id)
        .maybe_single()
        .execute()
    )
    # supabase-py returns resp.data=None when not found
    return resp.data if getattr(resp, "data", None) else None


def _get_signed_url(path: str, expires_in: int = 900) -> str:
    """
    Supabase signing expects a bucket-relative key. Never include the bucket name
    in the key and never include a leading slash.
    """
    key = (path or "").lstrip("/")
    if key.startswith(f"{SUPABASE_BUCKET}/"):
        key = key[len(f"{SUPABASE_BUCKET}/"):]
    # sign
    resp = supabase.storage.from_(SUPABASE_BUCKET).create_signed_url(key, expires_in)
    signed = None
    if isinstance(resp, dict):
        signed = resp.get("signedURL") or resp.get("signed_url")
    if not signed:
        raise HTTPException(404, f"Object not found for key={key}")
    return signed if signed.startswith("http") \
        else f"{SUPABASE_URL}/storage/v1/object/sign/{SUPABASE_BUCKET}/{key}?{signed}"

async def _download_bytes(url: str) -> bytes:
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.get(url)
        r.raise_for_status()
        return r.content

async def _call_handwritingocr(image_bytes: bytes, signed_url: str) -> dict:
    """
    Try shapes: multipart (file), JSON {url}, JSON {image_base64}.
    Log statuses to help debug provider errors.
    """
    if HANDWRITINGOCR_MOCK:
        return {"text": "[MOCK OCR] Replace with real OCR. This text is returned because HANDWRITINGOCR_MOCK=1."}

    header_sets = [
        {"x-api-key": HANDWRITINGOCR_API_KEY, "X-API-KEY": HANDWRITINGOCR_API_KEY},
        {"apikey": HANDWRITINGOCR_API_KEY, "Api-Key": HANDWRITINGOCR_API_KEY},
        {"Authorization": f"Bearer {HANDWRITINGOCR_API_KEY}", "x-api-key": HANDWRITINGOCR_API_KEY},
    ]
    attempts = []
    # best-effort filename/ctype from URL
    filename = "upload"
    ctype = "application/octet-stream"
    try:
        path = signed_url.split("?")[0]
        guess_type, _ = mimetypes.guess_type(path)
        if guess_type:
            ctype = guess_type
        # derive filename with extension if present
        base = path.rsplit("/", 1)[-1]
        if base:
            filename = base
    except Exception:
        pass

    file_field_candidates = [
        HANDWRITINGOCR_FILE_FIELD,
        "file",
        "image",
        "image_file",
    ]
    url_field_candidates = [
        HANDWRITINGOCR_URL_FIELD,
        "url",
        "image_url",
    ]
    b64_field_candidates = [
        HANDWRITINGOCR_B64_FIELD,
        "image_base64",
        "imageBase64",
        "b64",
    ]
    async with httpx.AsyncClient(timeout=120) as client:
        # If not in debug mode and a specific method is configured, use only that exact combo
        if not HANDWRITINGOCR_DEBUG and HANDWRITINGOCR_METHOD != "auto":
            H = header_sets[0]
            b64 = base64.b64encode(image_bytes).decode("ascii")
            method = HANDWRITINGOCR_METHOD
            try:
                if method == "multipart":
                    files = {HANDWRITINGOCR_FILE_FIELD: (filename, image_bytes, ctype)}
                    r = await client.post(HANDWRITINGOCR_ENDPOINT, headers=H, files=files)
                elif method == "json_url":
                    r = await client.post(HANDWRITINGOCR_ENDPOINT, headers=H, json={HANDWRITINGOCR_URL_FIELD: signed_url})
                elif method == "json_b64":
                    r = await client.post(HANDWRITINGOCR_ENDPOINT, headers=H, json={HANDWRITINGOCR_B64_FIELD: b64})
                elif method == "form_url":
                    r = await client.post(
                        HANDWRITINGOCR_ENDPOINT,
                        headers={**H, "Content-Type": "application/x-www-form-urlencoded"},
                        data={HANDWRITINGOCR_URL_FIELD: signed_url},
                    )
                elif method == "form_b64":
                    r = await client.post(
                        HANDWRITINGOCR_ENDPOINT,
                        headers={**H, "Content-Type": "application/x-www-form-urlencoded"},
                        data={HANDWRITINGOCR_B64_FIELD: b64},
                    )
                elif method == "get_url":
                    r = await client.get(HANDWRITINGOCR_ENDPOINT, headers=H, params={HANDWRITINGOCR_URL_FIELD: signed_url})
                else:
                    raise RuntimeError(f"Unsupported HANDWRITINGOCR_METHOD: {method}")

                if r.status_code == 200:
                    return r.json()
                attempts.append((f"exact:{method}", r.status_code, r.text[:500]))
            except Exception as e:
                attempts.append((f"exact-exc:{method}", None, str(e)))
            # Fail fast in non-debug mode
            logger.info("OCR attempts: %s", json.dumps(attempts, ensure_ascii=False))
            raise RuntimeError(
                f"OCR provider rejected configured method {HANDWRITINGOCR_METHOD}; first={attempts[0] if attempts else 'n/a'}"
            )

        for H in header_sets:
            # (1) multipart file with multiple field name candidates
            for fld in file_field_candidates:
                try:
                    files = {fld: (filename, image_bytes, ctype)}
                    r = await client.post(HANDWRITINGOCR_ENDPOINT, headers=H, files=files)
                    if r.status_code == 200:
                        return r.json()
                    attempts.append((f"multipart:{fld}", r.status_code, r.text[:500]))
                except Exception as e:
                    attempts.append((f"multipart-exc:{fld}", None, str(e)))
            # (2) JSON {url}
            for fld in url_field_candidates:
                try:
                    r = await client.post(HANDWRITINGOCR_ENDPOINT, headers=H, json={fld: signed_url})
                    if r.status_code == 200:
                        return r.json()
                    attempts.append((f"json-url:{fld}", r.status_code, r.text[:500]))
                except Exception as e:
                    attempts.append((f"json-url-exc:{fld}", None, str(e)))
            # (2b) x-www-form-urlencoded {url}
            for fld in url_field_candidates:
                try:
                    r = await client.post(HANDWRITINGOCR_ENDPOINT, headers={**H, "Content-Type": "application/x-www-form-urlencoded"}, data={fld: signed_url})
                    if r.status_code == 200:
                        return r.json()
                    attempts.append((f"form-url:{fld}", r.status_code, r.text[:500]))
                except Exception as e:
                    attempts.append((f"form-url-exc:{fld}", None, str(e)))
            # (2c) GET ?url=...
            try:
                r = await client.get(HANDWRITINGOCR_ENDPOINT, headers=H, params={"url": signed_url, "apikey": HANDWRITINGOCR_API_KEY})
                if r.status_code == 200:
                    return r.json()
                attempts.append(("get-url:url", r.status_code, r.text[:500]))
            except Exception as e:
                attempts.append(("get-url-exc:url", None, str(e)))
            # (3) JSON {image_base64}
            b64 = base64.b64encode(image_bytes).decode("ascii")
            for fld in b64_field_candidates:
                try:
                    r = await client.post(HANDWRITINGOCR_ENDPOINT, headers=H, json={fld: b64})
                    if r.status_code == 200:
                        return r.json()
                    attempts.append((f"json-b64:{fld}", r.status_code, r.text[:500]))
                except Exception as e:
                    attempts.append((f"json-b64-exc:{fld}", None, str(e)))
            # (3b) form {image_base64}
            for fld in b64_field_candidates:
                try:
                    r = await client.post(HANDWRITINGOCR_ENDPOINT, headers={**H, "Content-Type": "application/x-www-form-urlencoded"}, data={fld: b64})
                    if r.status_code == 200:
                        return r.json()
                    attempts.append((f"form-b64:{fld}", r.status_code, r.text[:500]))
                except Exception as e:
                    attempts.append((f"form-b64-exc:{fld}", None, str(e)))
    logger.info("OCR attempts: %s", json.dumps(attempts, ensure_ascii=False))
    raise RuntimeError(f"OCR provider rejected all shapes; first={attempts[0] if attempts else 'n/a'}")

def _parse_text(api_json: dict) -> tuple[str, dict]:
    text = ""
    if isinstance(api_json, dict):
        if isinstance(api_json.get("text"), str):
            text = api_json["text"]
        elif isinstance(api_json.get("pages"), list):
            text = "\n".join(p.get("text", "") for p in api_json["pages"])
    return text.strip(), api_json

# ── API ────────────────────────────────────────────────────────────────────────
class StartOCRBody(BaseModel):
    upload_id: str

@app.post("/api/ocr/start")
async def start_ocr(
    body: StartOCRBody,
    x_owner_id: Optional[str] = Header(None),
    x_user_id: Optional[str] = Header(None),
):
    _require_supabase_config()

    # 0) Validate input early
    if not body or not getattr(body, "upload_id", None):
        raise HTTPException(status_code=400, detail="upload_id is required")
    try:
        UUID(str(body.upload_id))
    except Exception:
        raise HTTPException(status_code=400, detail="upload_id must be a UUID")

    caller_id = x_owner_id or x_user_id

    # 1) Read upload row safely (catch PostgREST/API errors and turn into 400)
    try:
        resp = (
            supabase.table("uploads")
            .select("*")
            .eq("id", body.upload_id)
            .maybe_single()     # avoids raising when not found
            .execute()
        )
    except APIError as e:
        # Bad filter / malformed request -> 400
        raise HTTPException(status_code=400, detail=f"Invalid request: {getattr(e, 'message', str(e))}")

    row = getattr(resp, "data", None)
    if not row:
        raise HTTPException(status_code=404, detail="Upload not found")

    if not _owner_matches(row, caller_id):
        logger.warning(
            "auth forbid: caller=%s owner=%s user=%s",
            caller_id, row.get("owner_id"), row.get("user_id"),
        )
        raise HTTPException(status_code=403, detail="Forbidden")

    storage_path = row.get("storage_path")
    if not storage_path:
        raise HTTPException(status_code=400, detail="Missing storage_path")

    # 2) Mark processing
    _mark_status(row["id"], "processing", {
        "ocr_started_at": datetime.now(timezone.utc).isoformat(),
        "ocr_error": None,
    })
    # reflect locally for tests using in-memory rows
    row["status"] = "processing"
    row["ocr_started_at"] = row.get("ocr_started_at") or datetime.now(timezone.utc).isoformat()
    row["ocr_error"] = None

    # 3) Download and send to OCR via provider-agnostic service
    signed = _get_signed_url(storage_path)
    attempts_log: list = []
    last_err: str | None = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            # With OCR_MOCK=1 we skip downloading but still call the provider wrapper
            blob = b"" if os.getenv("OCR_MOCK") == "1" else await _download_bytes(signed)
            result = await ocr_extract_text(image_bytes=blob)

            text = (result.get("text") or "").strip()
            meta = result
            if not text:
                raise ValueError("OCR returned empty text")

            attempts_log.append(200)  # success

            # Persist OCR result record
            try:
                supabase.table("ocr_results").insert({
                    "upload_id": row["id"],
                    "text": text,
                    "status": "OCR_DONE",
                    "provider": os.environ.get("OCR_PROVIDER", "mock"),
                    "attempts_log": json.dumps(attempts_log),
                }).execute()
            except Exception as e:
                logger.warning("failed to insert ocr_results: %s", e)

            # Mark upload as done
            _mark_status(row["id"], "OCR_DONE", {
                "extracted_text": text,
                "ocr_completed_at": datetime.now(timezone.utc).isoformat(),
                "ocr_meta": json.dumps(meta),
            })
            row["status"] = "OCR_DONE"
            row["extracted_text"] = text
            row["ocr_completed_at"] = row.get("ocr_completed_at") or datetime.now(timezone.utc).isoformat()
            row["ocr_meta"] = json.dumps(meta)
            return {"ok": True, "status": "done", "upload_id": row["id"]}

        except httpx.ReadTimeout:
            last_err = "timeout"
            attempts_log.append("timeout")
            await asyncio.sleep(1.5)  # backoff
        except httpx.HTTPStatusError as e:
            last_err = f"http {getattr(e.response, 'status_code', 500)}"
            attempts_log.append(getattr(e.response, "status_code", 500))
            await asyncio.sleep(1.5)  # backoff
        except Exception as e:
            last_err = str(e)
            attempts_log.append("error")
            await asyncio.sleep(1.5)

    # Persist error result and mark upload as OCR_ERROR
    try:
        supabase.table("ocr_results").insert({
            "upload_id": row["id"],
            "text": None,
            "status": "OCR_ERROR",
            "provider": os.environ.get("OCR_PROVIDER", "mock"),
            "attempts_log": json.dumps(attempts_log),
        }).execute()
    except Exception as e:
        logger.warning("failed to insert error ocr_results: %s", e)

    _mark_status(row["id"], "OCR_ERROR", {"ocr_error": last_err})
    row["status"] = "OCR_ERROR"
    row["ocr_error"] = last_err
    # Provider/network errors should be 502 (bad gateway), not 500
    raise HTTPException(status_code=502, detail=f"OCR pipeline error: {last_err or 'unknown'}")


@app.get("/api/ocr/status/{upload_id}")
async def ocr_status(
    upload_id: str,
    x_owner_id: Optional[str] = Header(None),
    x_user_id: Optional[str] = Header(None),
):
    _require_supabase_config()
    caller_id = x_owner_id or x_user_id
    resp = supabase.table("uploads").select("*").eq("id", upload_id).maybe_single().execute()
    row = resp.data
    if not row:
        raise HTTPException(404, "Upload not found")
    if not _owner_matches(row, caller_id):
        raise HTTPException(403, "Forbidden")
    status_val = row.get("status", "pending")
    if status_val == "OCR_DONE":
        status_val = "done"
    return {
        "status": status_val,
        "extracted_text": row.get("extracted_text"),
        "error": row.get("ocr_error"),
        "started": row.get("ocr_started_at"),
        "completed": row.get("ocr_completed_at"),
    }


# 
# Grading API
#

class StartGradeBody(BaseModel):
    upload_id: str


@app.post("/api/grade")
async def start_grade(
    body: StartGradeBody,
    x_owner_id: Optional[str] = Header(None),
    x_user_id: Optional[str] = Header(None),
):
    _require_supabase_config()
    caller_id = x_owner_id or x_user_id

    # 1) Fetch upload and authz
    resp = (
        supabase.table("uploads")
        .select("*")
        .eq("id", body.upload_id)
        .maybe_single()
        .execute()
    )
    row = resp.data
    if not row:
        raise HTTPException(404, "Upload not found")
    if not _owner_matches(row, caller_id):
        raise HTTPException(403, "Forbidden")

    # 2) Ensure we have OCR text (perform OCR inline if missing)
    text = (row.get("extracted_text") or "").strip()
    if not text:
        storage_path = row.get("storage_path")
        if not storage_path:
            raise HTTPException(400, "Missing storage_path")

        _mark_status(row["id"], "processing", {"ocr_error": None})
        row["status"] = "processing"
        row["ocr_error"] = None
        signed = _get_signed_url(storage_path)

        attempts_log: list = []
        try:
            blob = b"" if os.getenv("OCR_MOCK") == "1" else await _download_bytes(signed)
            result = await ocr_extract_text(image_bytes=blob)
            text = (result.get("text") or "").strip()
            meta = result
            if not text:
                raise ValueError("OCR returned empty text")

            attempts_log.append(200)

            try:
                supabase.table("ocr_results").insert({
                    "upload_id": row["id"],
                    "text": text,
                    "status": "OCR_DONE",
                    "provider": os.environ.get("OCR_PROVIDER", "mock"),
                    "attempts_log": json.dumps(attempts_log),
                }).execute()
            except Exception as e:
                logger.warning("failed to insert ocr_results: %s", e)

            _mark_status(row["id"], "OCR_DONE", {
                "extracted_text": text,
                "ocr_completed_at": datetime.now(timezone.utc).isoformat(),
                "ocr_meta": json.dumps(meta),
            })
            row["status"] = "OCR_DONE"
            row["extracted_text"] = text
            row["ocr_completed_at"] = row.get("ocr_completed_at") or datetime.now(timezone.utc).isoformat()
            row["ocr_meta"] = json.dumps(meta)

        except httpx.ReadTimeout:
            attempts_log.append("timeout")
            try:
                supabase.table("ocr_results").insert({
                    "upload_id": row["id"],
                    "text": None,
                    "status": "OCR_ERROR",
                    "provider": os.environ.get("OCR_PROVIDER", "mock"),
                    "attempts_log": json.dumps(attempts_log),
                }).execute()
            except Exception as e:
                logger.warning("failed to insert ocr_results: %s", e)
            _mark_status(row["id"], "OCR_ERROR", {"ocr_error": "timeout"})
            row["status"] = "OCR_ERROR"
            row["ocr_error"] = "timeout"
            raise HTTPException(status_code=502, detail="OCR failed: timeout")

        except httpx.HTTPStatusError as e:
            code = getattr(e.response, "status_code", 500)
            attempts_log.append(code)
            try:
                supabase.table("ocr_results").insert({
                    "upload_id": row["id"],
                    "text": None,
                    "status": "OCR_ERROR",
                    "provider": os.environ.get("OCR_PROVIDER", "mock"),
                    "attempts_log": json.dumps(attempts_log),
                }).execute()
            except Exception as e2:
                logger.warning("failed to insert ocr_results: %s", e2)
            _mark_status(row["id"], "OCR_ERROR", {"ocr_error": f"http {code}"})
            row["status"] = "OCR_ERROR"
            row["ocr_error"] = f"http {code}"
            raise HTTPException(status_code=500, detail=f"OCR failed: http {code}")

        except Exception as e:
            attempts_log.append("error")
            try:
                supabase.table("ocr_results").insert({
                    "upload_id": row["id"],
                    "text": None,
                    "status": "OCR_ERROR",
                    "provider": os.environ.get("OCR_PROVIDER", "mock"),
                    "attempts_log": json.dumps(attempts_log),
                }).execute()
            except Exception as e3:
                logger.warning("failed to insert ocr_results: %s", e3)
            _mark_status(row["id"], "OCR_ERROR", {"ocr_error": str(e)})
            row["status"] = "OCR_ERROR"
            row["ocr_error"] = str(e)
            raise HTTPException(status_code=500, detail=f"OCR failed: {e}")

    # 3) Parse -> autokey -> grade
    questions = parse_questions(text)
    keys = generate_autokeys(questions)
    result: GradeResult = grade(questions, keys, text)
    result.submission_id = row["id"]

    # mark needs_review if OCR looked weak
    ocr_meta_raw = row.get("ocr_meta")
    try:
        ocr_meta = json.loads(ocr_meta_raw) if isinstance(ocr_meta_raw, str) else (ocr_meta_raw or {})
    except Exception:
        ocr_meta = {}
    if len(text) < 12:
        result.needs_review = True

    # Stamp versions
    result.rubric_version = RUBRIC_VERSION
    result.prompt_version = PROMPT_VERSION

    # 4) Build overlay and (placeholder) PDF
    overlay = build_overlay_for_result(result)
    summary = (
        f"Submission: {row['id']}\n"
        f"Total: {result.total_score}/{result.total_max}\n"
        f"Rubric v{result.rubric_version} | Prompt v{result.prompt_version}\n"
        f"Needs review: {result.needs_review}"
    )
    pdf_bytes = flatten_to_pdf(summary, overlay)

    # 5) Store artifacts in Supabase Storage (graded-pdfs bucket)
    owner_id = row.get("owner_id") or caller_id or "unknown"
    overlay_key = f"{owner_id}/{row['id']}.overlay.json"
    pdf_key = f"{owner_id}/{row['id']}.pdf"

    try:
        supabase.storage.from_("graded-pdfs").upload(
            overlay_key,
            json.dumps(overlay.model_dump()).encode("utf-8"),
        )
    except Exception as e:
        logger.warning("overlay upload failed: %s", e)

    try:
        supabase.storage.from_("graded-pdfs").upload(
            pdf_key,
            pdf_bytes,
        )
    except Exception as e:
        logger.warning("pdf upload failed: %s", e)

    # 6) Update DB row with grading metadata
    try:
        supabase.table("uploads").update({
            "rubric_version": result.rubric_version,
            "prompt_version": result.prompt_version,
            "needs_review": result.needs_review,
            "graded_pdf_path": pdf_key,
            "overlay_path": overlay_key,
            "grade_json": json.dumps(result.model_dump()),
        }).eq("id", row["id"]).execute()
    except Exception as e:
        logger.warning("uploads update failed: %s", e)

    return {
        "ok": True,
        "upload_id": row["id"],
        "rubric_version": result.rubric_version,
        "prompt_version": result.prompt_version,
        "needs_review": result.needs_review,
        "overlay_path": overlay_key,
        "graded_pdf_path": pdf_key,
        "grade": result.model_dump(),
    }

@app.options("/api/grade")
async def grade_options() -> Response:
    return Response(status_code=200)


# Upload deletion (storage-first, then DB)
@app.delete("/api/uploads/{upload_id}")
async def delete_upload(upload_id: str):
    if not _is_uuid(upload_id):
        raise HTTPException(status_code=400, detail="upload_id must be a UUID")

    try:
        row = _select_upload_row(supabase, upload_id)
    except APIError as e:
        raise HTTPException(status_code=400, detail=f"Invalid request: {getattr(e, 'message', str(e))}")

    if not row:
        # Nothing to delete
        raise HTTPException(status_code=404, detail="Upload not found")

    storage_path = row.get("storage_path")

    # 1) Delete from storage first (if present)
    try:
        if storage_path:
            supabase.storage.from_(SUPABASE_BUCKET).remove([storage_path])
    except Exception:
        # Storage errors shouldn't leave DB row orphaned; log but continue
        logger.warning("Storage delete failed for %s", storage_path, exc_info=True)

    # 2) Delete DB row
    try:
        supabase.table("uploads").delete().eq("id", upload_id).execute()
    except APIError as e:
        raise HTTPException(status_code=400, detail=f"DB delete failed: {getattr(e, 'message', str(e))}")

    return {"ok": True, "deleted": upload_id}
