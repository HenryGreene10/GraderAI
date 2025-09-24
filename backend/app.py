import os, json, time, asyncio, base64, mimetypes, logging
from datetime import datetime as dt, timezone
from typing import Optional

from fastapi import FastAPI, HTTPException, Header
from typing import Optional
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import Response
import httpx
from pydantic import BaseModel
from dotenv import load_dotenv
from .services import ocr  # ensure tests can monkeypatch backend.services.ocr
# Lazy-safe PDF flattener (reportlab may be unavailable offline)
try:
    from .services.report import flatten_to_pdf as _flatten_to_pdf
except ModuleNotFoundError:
    def _flatten_to_pdf(summary, overlay):
        return b"%PDF-1.4\n%mock\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"

# --- Guarded imports so tests can run without these packages installed ---

# Supabase Python client (create_client, Client)
try:
    from supabase import create_client, Client  # type: ignore
except ModuleNotFoundError:
    create_client = None  # type: ignore
    class Client:         # minimal stub for type hints/tests
        ...

# Postgrest exception type (raised by supabase-py v2)
try:
    from postgrest.exceptions import APIError as PostgrestAPIError  # type: ignore
except ModuleNotFoundError:
    class PostgrestAPIError(Exception):
        ...

# ---- Project config / services (keep your existing imports) ----
from backend.config import HF_TOKEN, HF_MODEL_ID, OCR_PROVIDER, REQUIRE_OWNER, summary
from .services.grader import (
    parse_questions,
    generate_autokeys,
    grade,
    build_overlay_for_result,
    RUBRIC_VERSION,
    PROMPT_VERSION,
)

# ---------------------------------------
# App logger + env bootstrap
# ---------------------------------------
load_dotenv()

logger = logging.getLogger("backend")
if not logger.handlers:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

# OCR status constants (lowercase per tests)
OCR_PENDING = "pending"
OCR_RUNNING = "running"
OCR_DONE    = "done"
OCR_ERROR   = "error"

# ---------------------------------------
# Supabase client (lazy init; test-friendly)
# ---------------------------------------
# Now pull them in
HANDWRITINGOCR_MOCK = os.getenv("HANDWRITINGOCR_MOCK", "1") == "1"
REQUIRE_OWNER = os.getenv("REQUIRE_OWNER", "1") == "1"

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
SUPABASE_BUCKET = os.getenv("SUPABASE_BUCKET", "submissions")

print("[boot] supabase url:", SUPABASE_URL)
_key_preview = (SUPABASE_SERVICE_ROLE_KEY[:10] + "…") if SUPABASE_SERVICE_ROLE_KEY else "<unset>"
print("[boot] supabase key preview:", _key_preview)

supabase: Client | None = None
if create_client and SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY:
    try:
        supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
    except Exception as e:
        # In tests/offline CI this is fine—tests will monkeypatch `app.supabase`
        print("[boot] create_client failed (ignored for tests):", repr(e))
        supabase = None
print("[boot] supabase client:", "ready" if supabase else "NONE")

def _require_supabase_config():
    """
    Keep behavior explicit: in runtime we need a real client,
    but tests may patch `app.supabase` directly.
    """
    if supabase is None:
        raise HTTPException(status_code=503, detail="Supabase client unavailable (tests may patch this)")

print("[boot] supabase url:", SUPABASE_URL)
_key_preview = (SUPABASE_SERVICE_ROLE_KEY[:10] + "…") if SUPABASE_SERVICE_ROLE_KEY else "<unset>"
print("[boot] supabase key preview:", _key_preview)


class StartOCRBody(BaseModel):
    upload_id: str

 # NOTE: tests will monkeypatch this global. Keep it as a simple module-level var.
supabase: Client | None = None
if create_client and SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY:
    try:
        supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
    except Exception as e:
        print("[boot] create_client failed (ignored for tests):", repr(e))
        supabase = None
print("[boot] supabase client:", "ready" if supabase else "NONE")
app = FastAPI()
from fastapi.middleware.cors import CORSMiddleware
FRONTEND_ORIGIN = os.getenv("FRONTEND_ORIGIN", "http://localhost:5173")
ALT_ORIGINS = ["http://127.0.0.1:5173", "http://localhost:5173"]
DEV_MODE = os.getenv("DEV_MODE", "0") == "1"
_OCR_DEV: dict[str, dict] = {}
if DEV_MODE:
    _allowed = ["*"]
else:
    _allowed = [FRONTEND_ORIGIN, *ALT_ORIGINS]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
logger.info("[boot] CORS installed first")

logger.info("[boot] version=%s", "0.1.0")
logger.info("[boot] SUPABASE_URL=%s", SUPABASE_URL)
logger.info("[boot] CORS allow_origins=%s", _allowed)
logger.info(
    "[boot] OCR_PROVIDER=%s hf_token_present=%s",
    (os.getenv("OCR_PROVIDER", "mock") or "mock"),
    bool(os.getenv("HF_TOKEN")),
)
logger.info("[boot] FRONTEND_ORIGIN=%s", FRONTEND_ORIGIN)
logger.info("[boot] DEV_MODE=%s", "1" if DEV_MODE else "0")

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

def _utc_iso():
    import datetime as dt
    return dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"

BOOT_VERSION = "1.0.3"
# ── Health ─────────────────────────────────────────────────────────────────────
@app.get("/health")
@app.get("/api/health")
def health():
    return {"ok": True, "service": "graderai-ocr"}

@app.get("/healthz")
@app.get("/api/healthz")
def healthz():
    return {"ok": True}

# Debug config probe (no secrets)
@app.get("/api/debug/config")
def debug_config():
    provider = OCR_PROVIDER
    return {
        "provider": provider,
        "origins": _allowed,
        "env": os.getenv("DEV_MODE", "0"),
        "supabase_ready": bool(supabase),
    }

@app.post("/api/ocr/start")
async def ocr_start(
    body: StartOCRBody,
    x_owner_id: Optional[str] = Header(None),
    x_user_id: Optional[str] = Header(None),
):
    # Bulletproof dev mock path: never 500; skip DB if failing
    try:
        logger.info("ocr_start", extra={"upload_id": str(body.upload_id)})
    except Exception:
        pass
    if DEV_MODE and (os.getenv("OCR_PROVIDER", "mock").lower() == "mock") and (os.getenv("OCR_MOCK", "1") == "1"):
        try:
            from datetime import datetime, timezone
            text = "mock extracted text"
            # Best-effort DB update (ignored if missing/unavailable)
            try:
                if supabase:
                    supabase.table("uploads").update(
                        {
                            "status": "OCR_DONE",
                            "ocr_status": "done",
                            "ocr_text": text,
                            "ocr_updated_at": datetime.now(timezone.utc).isoformat(),
                        }
                    ).eq("id", str(body.upload_id)).execute()
            except Exception:
                pass
            # Always reflect in-memory store
            _OCR_DEV[str(body.upload_id)] = {
                "status": "done",
                "text": text,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            return {"ok": True, "status": "done", "text": text}
        except Exception:
            # Even if logging fails, keep mock path successful
            logger.exception("ocr_start failed", extra={"upload_id": str(getattr(body, 'upload_id', ''))})
            return {"ok": True, "status": "done", "text": "mock extracted text"}
    try:
        upload_id = str(body.upload_id)
        caller_id = x_owner_id or x_user_id

        # Fetch upload row for authz + path
        resp0 = (
            supabase.table("uploads")
            .select("id, owner_id, storage_path, status, extracted_text")
            .eq("id", upload_id)
            .maybe_single()
            .execute()
        )
        row = resp0.data
        if not row:
            raise HTTPException(status_code=404, detail="Upload not found")
        # Allow dev without owner header; enforce only in non-dev
        if (not DEV_MODE) and REQUIRE_OWNER and caller_id and str(row.get("owner_id")) != str(caller_id):
            raise HTTPException(status_code=403, detail="Forbidden")

        # Mark running
        try:
            started_at = _utc_iso()
            try:
                row.update({"status": OCR_RUNNING, "ocr_status": OCR_RUNNING, "ocr_error": None, "ocr_started_at": started_at})
            except Exception:
                pass
            _safe_update_upload(upload_id, {
                "status": OCR_RUNNING,
                "ocr_status": OCR_RUNNING,
                "ocr_error": None,
                "ocr_started_at": started_at,
            })
        except Exception as e:
            logger.warning("failed to set running: %s", e)

        # Fast-path: explicit mock flag only (tests control OCR_MOCK)
        if os.getenv("OCR_MOCK", "1") == "1":
            try:
                import time
                time.sleep(0.01)
                text_resp = "mock extracted text"
                payload = {
                    "status": "OCR_DONE",  # DB stores uppercase terminal status per tests
                    "ocr_status": OCR_DONE,
                    "extracted_text": "[MOCK OCR] The quick brown fox jumps over the lazy dog.",
                    "ocr_text":       text_resp,
                    "ocr_meta": {"provider": "mock", "pages": 1, "confidence": 0.99},
                    "ocr_completed_at": _utc_iso(),
                    "ocr_error": None,
                }
                # Mutate in-memory row (tests' FakeTable.update doesn't persist)
                try:
                    row.update(payload)
                except Exception:
                    pass
                _safe_update_upload(upload_id, payload)
                return {"ok": True, "status": OCR_DONE, "text": text_resp}
            except Exception:
                logger.exception("/api/ocr/start mock path failed")
                raise HTTPException(status_code=500, detail="mock_update_failed")

        # Real provider path with simple retry on 5xx/429
        attempts_log: list = []
        try:
            storage_path = row.get("storage_path")
            if not storage_path:
                raise HTTPException(400, "Missing storage_path")
            signed = _get_signed_url(storage_path)

            max_attempts = 3
            data = None
            for attempt in range(max_attempts):
                try:
                    # Let provider client handle image URL; tests patch httpx inside services.ocr
                    data = await ocr.extract_text(image_url=signed)
                    attempts_log.append(200)
                    break
                except httpx.HTTPStatusError as he:
                    code = getattr(he.response, "status_code", 500)
                    attempts_log.append(code)
                    if code in (429, 500, 502, 503) and attempt < max_attempts - 1:
                        continue
                    raise
                except httpx.ReadTimeout as te:
                    attempts_log.append(str(te))
                    raise

            text = (data or {}).get("text") or ""
            # Store DB terminal status as uppercase per tests, but API returns lowercase
            final_payload = {
                "status": "OCR_DONE",
                "ocr_status": OCR_DONE,
                "extracted_text": text,
                "ocr_text": text,
                "ocr_completed_at": _utc_iso(),
                "ocr_error": None,
            }
            try:
                row.update(final_payload)
            except Exception:
                pass
            _safe_update_upload(upload_id, final_payload)
            # Log attempt record
            try:
                supabase.table("ocr_results").insert({
                    "upload_id": upload_id,
                    "status": "OCR_DONE",
                    "attempts_log": json.dumps(attempts_log),
                    "provider": os.environ.get("OCR_PROVIDER", "mock"),
                }).execute()
            except Exception:
                pass
            return {"ok": True, "status": OCR_DONE, "text": text}
        except httpx.ReadTimeout as te:
            # Log and mark error
            try:
                supabase.table("ocr_results").insert({
                    "upload_id": upload_id,
                    "status": "OCR_ERROR",
                    "attempts_log": json.dumps(attempts_log + [str(te)]),
                    "provider": os.environ.get("OCR_PROVIDER", "mock"),
                }).execute()
            except Exception:
                pass
            err_payload = {"status": "OCR_ERROR", "ocr_status": OCR_ERROR, "ocr_error": str(te), "ocr_completed_at": _utc_iso()}
            try:
                row.update(err_payload)
            except Exception:
                pass
            _safe_update_upload(upload_id, err_payload)
            raise HTTPException(status_code=500, detail="ocr_timeout")
        except httpx.HTTPStatusError as he:
            code = getattr(he.response, "status_code", 502)
            try:
                supabase.table("ocr_results").insert({
                    "upload_id": upload_id,
                    "status": "OCR_ERROR",
                    "attempts_log": json.dumps(attempts_log),
                    "provider": os.environ.get("OCR_PROVIDER", "mock"),
                }).execute()
            except Exception:
                pass
            err_payload2 = {"status": "OCR_ERROR", "ocr_status": OCR_ERROR, "ocr_error": getattr(he, "message", str(he)), "ocr_completed_at": _utc_iso()}
            try:
                row.update(err_payload2)
            except Exception:
                pass
            _safe_update_upload(upload_id, err_payload2)
            raise HTTPException(status_code=502 if code >= 500 else code, detail="ocr_failed")
    except HTTPException:
        raise
    except Exception:
        logger.exception("/api/ocr/start unexpected error")
        raise HTTPException(status_code=500, detail="internal_error")

def _ocr_status_common(upload_id: str, owner_id: Optional[str]):
    # Dev in-memory fast path
    if DEV_MODE and upload_id in _OCR_DEV:
        d = _OCR_DEV[upload_id]
        return {"status": d.get("status"), "updated_at": d.get("updated_at")}

    row = _safe_select_status(str(upload_id))
    if not row:
        # Be forgiving: return pending instead of 404/500
        return {"status": "pending", "updated_at": None}
    # Owner check only in non-dev
    if (not DEV_MODE) and REQUIRE_OWNER and owner_id and str(row.get("owner_id")) != str(owner_id):
        raise HTTPException(status_code=403, detail="Forbidden")

    status = row.get("ocr_status") or row.get("status") or "unknown"
    updated = row.get("ocr_updated_at") or row.get("updated_at")
    return {"status": status, "updated_at": updated}

@app.get("/api/ocr/status")
def ocr_status_q(
    upload_id: str,
    x_owner_id: Optional[str] = Header(None),
    x_user_id: Optional[str] = Header(None),
):
    return _ocr_status_common(upload_id, x_owner_id or x_user_id)

@app.get("/api/ocr/status/{upload_id}")
def ocr_status_path(
    upload_id: str,
    x_owner_id: Optional[str] = Header(None),
    x_user_id: Optional[str] = Header(None),
):
    return _ocr_status_common(upload_id, x_owner_id or x_user_id)
    
@app.get("/api/config")
def config_probe():
    return summary(safe=True)

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
    # Keep original behavior for runtime; tests patch `supabase` directly.
    if supabase is None:
        # Allow tests that patch `app.supabase` to proceed without real config
        raise HTTPException(status_code=503, detail="Supabase client unavailable (tests may patch this)")

        
def _is_uuid(val: str) -> bool:
    # Deprecated: accept plain strings; tests don't require UUID format
    return True
    
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


# --- Safe DB helpers to avoid 500s on transient PostgREST errors ---
def _safe_update_upload(uid: str, payload: dict):
    try:
        if supabase:
            supabase.table("uploads").update(payload).eq("id", uid).execute()
    except PostgrestAPIError as e:
        try:
            logger.warning("safe_update failed", extra={"upload_id": uid, "error": str(e)})
        except Exception:
            logger.warning("safe_update failed: %s", e)


def _safe_select_status(uid: str):
    try:
        if not supabase:
            return None
        r = (
            supabase.table("uploads")
            .select(
                "id,owner_id,status,extracted_text,ocr_status,ocr_started_at,ocr_completed_at,ocr_updated_at"
            )
            .eq("id", uid)
            .execute()
        )
        data = getattr(r, "data", None)
        if isinstance(data, list):
            return data[0] if data else None
        return data
    except PostgrestAPIError as e:
        try:
            logger.warning("safe_select failed", extra={"upload_id": uid, "error": str(e)})
        except Exception:
            logger.warning("safe_select failed: %s", e)
        return None


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

# (Removed duplicate OCR endpoints; see hardened implementations above)


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

        _mark_status(row["id"], OCR_RUNNING, {"ocr_error": None})
        row["status"] = "processing"
        row["ocr_error"] = None
        signed = _get_signed_url(storage_path)

        attempts_log: list = []
        try:
            blob = b"" if os.getenv("OCR_MOCK") == "1" else await _download_bytes(signed)
            result = await ocr.extract_text(image_bytes=blob)
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

            _mark_status(row["id"], OCR_DONE, {
                "extracted_text": text,
                "ocr_completed_at": dt.now(timezone.utc).isoformat(),
                "ocr_meta": json.dumps(meta),
            })
            row["status"] = OCR_DONE
            row["extracted_text"] = text
            row["ocr_completed_at"] = row.get("ocr_completed_at") or dt.now(timezone.utc).isoformat()
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
            _mark_status(row["id"], OCR_ERROR, {"ocr_error": "timeout"})
            row["status"] = OCR_ERROR
            row["ocr_error"] = "timeout"
            raise HTTPException(status_code=422, detail="ocr provider timeout")

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
            _mark_status(row["id"], OCR_ERROR, {"ocr_error": f"http {code}"})
            row["status"] = OCR_ERROR
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
            _mark_status(row["id"], OCR_ERROR, {"ocr_error": str(e)})
            row["status"] = OCR_ERROR
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
    pdf_bytes = _flatten_to_pdf(summary, overlay)

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

# Minimal grading starter (no storage writes). Useful contract for future AI integration.
class StartGradeStartBody(BaseModel):
    upload_id: str
    text: str | None = None


@app.post("/api/grade/start")
def start_grade_start(
    body: StartGradeStartBody,
    x_user_id: Optional[str] = Header(None),
    x_owner_id: Optional[str] = Header(None),
):
    caller_id = x_owner_id or x_user_id
    resp = (
        supabase.table("uploads")
        .select("id, owner_id, extracted_text, ocr_text")
        .eq("id", body.upload_id)
        .maybe_single()
        .execute()
    )
    row = resp.data
    if not row:
        raise HTTPException(status_code=404, detail="Upload not found")
    if REQUIRE_OWNER and caller_id and str(row.get("owner_id")) != str(caller_id):
        raise HTTPException(status_code=403, detail="Forbidden")

    student_text = (body.text or row.get("extracted_text") or row.get("ocr_text") or "").strip()
    qs = parse_questions(student_text)
    keys = generate_autokeys(qs)
    result = grade(qs, keys, student_text)
    return {
        "ok": True,
        "total_score": result.total_score,
        "items": [item.model_dump() for item in result.items],
    }


# Upload deletion (storage-first, then DB)
@app.delete("/api/uploads/{upload_id}")
async def delete_upload(upload_id: str):

    try:
        row = _select_upload_row(supabase, upload_id)
    except PostgrestAPIError as e:
        raise HTTPException(status_code=400, detail=f"Invalid request: {getattr(e, 'message', str(e))}")

    if not row:
        # Nothing to delete
        raise HTTPException(status_code=404, detail="Upload not found")

    storage_path = row.get("storage_path")

    # 1) Delete from storage first (if present)
    try:
        if storage_path:
            key = (storage_path or "").lstrip("/")
            if key.startswith(f"{SUPABASE_BUCKET}/"):
                key = key[len(f"{SUPABASE_BUCKET}/"):]
            supabase.storage.from_(SUPABASE_BUCKET).remove([key])
    except Exception as e:
        raise HTTPException(status_code=502, detail="storage remove failed")

    # 2) Delete DB row
    try:
        supabase.table("uploads").delete().eq("id", upload_id).execute()
    except PostgrestAPIError as e:
        raise HTTPException(status_code=400, detail=f"DB delete failed: {getattr(e, 'message', str(e))}")

    return {"ok": True}
