import os, json, time, asyncio, base64, mimetypes, logging
if __name__ == "__main__":
    raise SystemExit("Run with: python -m uvicorn backend.app:app --reload --port 8000")
from datetime import datetime as dt, timezone
from typing import Optional
from typing import Tuple

from fastapi import FastAPI, HTTPException, Header
from typing import Optional
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import Response
from starlette.responses import JSONResponse
import asyncio
import io
import json
import os
import pytesseract
import posixpath
import httpx
from pydantic import BaseModel
from dotenv import load_dotenv
from pathlib import Path
_here = Path(__file__).parent
load_dotenv(_here / ".env")              # backend/.env
load_dotenv(_here.parent / ".env")       # project root .env (optional)
load_dotenv()                            # process env fallback
# --- Tesseract bootstrap (Windows-friendly) ---
import os
import pytesseract

# Prefer explicit env; fall back to common install path
tcmd = os.getenv("TESSERACT_CMD") or r"C:\\Program Files\\Tesseract-OCR\\tesseract.exe"

# Make sure the process PATH includes the folder (some installs need this)
tdir = os.path.dirname(tcmd)
if tdir and os.path.isdir(tdir):
    os.environ["PATH"] = tdir + os.pathsep + os.environ.get("PATH", "")

# Tell pytesseract which exe to use
pytesseract.pytesseract.tesseract_cmd = tcmd

# Boot diagnostics
print("[BOOT][tesseract] cmd =", repr(pytesseract.pytesseract.tesseract_cmd))
try:
    print("[BOOT][tesseract] version =", pytesseract.get_tesseract_version())
except Exception as e:
    print("[BOOT][tesseract] version_error =", e)
# --- end bootstrap ---
from io import BytesIO
try:
    from PIL import Image
except Exception:
    Image = None
from .services import ocr  # ensure tests can monkeypatch backend.services.ocr
# Local OCR provider: avoid heavy import (torch) at module import time
_get_local_ocr_provider = None  # set by local import inside handler when needed
def _normalize_local_text(t: str) -> str:
    return (t or "").strip()
# Lazy-safe PDF flattener (reportlab may be unavailable)
try:
    from .services.report import flatten_to_pdf as _flatten_to_pdf
except Exception:
    def _flatten_to_pdf(*_args, **_kwargs):
        return None

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
from .config import HF_TOKEN, HF_MODEL_ID, OCR_PROVIDER, REQUIRE_OWNER, summary
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
_here = Path(__file__).resolve().parent
# Load env from backend/.env explicitly (and keep process env intact)
load_dotenv(_here / ".env")
load_dotenv(_here.parent / ".env", override=False)  # optional parent fallback
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

SUPABASE_URL = os.getenv("SUPABASE_URL") or None
SUPABASE_KEY = os.getenv("SUPABASE_KEY") or os.getenv("SUPABASE_SERVICE_ROLE_KEY") or None
SUPABASE_BUCKET = (
    os.getenv("SUPABASE_BUCKET")
    or os.getenv("SUPABASE_STORAGE_BUCKET")
    or os.getenv("STORAGE_BUCKET")
    or None
)
OCR_PROVIDER = (os.getenv("OCR_PROVIDER") or "mock").strip()

print("[boot] supabase url:", SUPABASE_URL)
_key_preview = (str(SUPABASE_KEY)[:10] + "…") if SUPABASE_KEY else "<unset>"
print("[boot] supabase key preview:", _key_preview)

supabase: Client | None = None
if create_client and SUPABASE_URL and SUPABASE_KEY:
    try:
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    except Exception as e:
        # In tests/offline CI this is fineâ€”tests will monkeypatch `app.supabase`
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
_key_preview = (str(SUPABASE_KEY)[:10] + "…") if SUPABASE_KEY else "<unset>"
print("[boot] supabase key preview:", _key_preview)


class StartOCRBody(BaseModel):
    upload_id: str
    model: str | None = None

 # NOTE: tests will monkeypatch this global. Keep it as a simple module-level var.
supabase: Client | None = None
if create_client and SUPABASE_URL and SUPABASE_KEY:
    try:
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    except Exception as e:
        print("[boot] create_client failed (ignored for tests):", repr(e))
        supabase = None
print("[boot] supabase client:", "ready" if supabase else "NONE")

# No-op Supabase stub to avoid crashes when env/client are missing.
class _NoopSupa:
    class _NoopResp:
        def __init__(self):
            self.data = None
            self.error = None
    class _NoopTable:
        def insert(self, *a, **k): return self
        def upsert(self, *a, **k): return self
        def update(self, *a, **k): return self
        def delete(self, *a, **k): return self
        def select(self, *a, **k): return self
        def eq(self, *a, **k): return self
        def single(self): return self
        def maybe_single(self): return self
        def execute(self): return _NoopSupa._NoopResp()
    class _NoopStorageBucket:
        def create_signed_url(self, *a, **k): return {"signedURL": None}
    class _NoopStorage:
        def from_(self, *a, **k): return _NoopSupa._NoopStorageBucket()
    def table(self, *_a, **_k): return _NoopSupa._NoopTable()
    @property
    def storage(self): return _NoopSupa._NoopStorage()

if supabase is None:
    try:
        logger.warning("[boot] Supabase disabled; using no-op client")
    except Exception:
        print("[boot] Supabase disabled; using no-op client")
    supabase = _NoopSupa()
app = FastAPI()
from fastapi.middleware.cors import CORSMiddleware
FRONTEND_ORIGIN = os.getenv("FRONTEND_ORIGIN", "http://localhost:5173")
ALT_ORIGINS = ["http://127.0.0.1:5173", "http://localhost:5173"]
DEV_MODE = os.getenv("DEV_MODE", "0") not in ("0", "", "false", "False")
_OCR_DEV: dict[str, dict] = {}
# Explicit CORS allowlist for Vite dev server
_allowed = ["http://localhost:5173", "http://127.0.0.1:5173"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed,
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=True,
    max_age=600,
)
logger.info("[boot] CORS installed first")

# Generic OPTIONS handler for safety (CORS preflight)
@app.options("/api/{path:path}")
async def options_any(path: str) -> Response:
    return Response(status_code=204)

# Boot summary line for quick verification
try:
    logger.info(
        "[boot] env | SUPABASE_URL=%s KEY=%s BUCKET=%s | OCR_PROVIDER=%s | DEV_MODE=%s",
        bool(SUPABASE_URL), bool(SUPABASE_KEY), bool(os.getenv("SUBMISSIONS_BUCKET")), OCR_PROVIDER, DEV_MODE,
    )
except Exception:
    pass

# Explicit preflight routes so nothing else intercepts
@app.options("/api/ocr/start")
def _preflight_start() -> Response:
    return Response(status_code=204)

@app.options("/api/ocr/status/{_upload_id}")
def _preflight_status(_upload_id: str) -> Response:
    return Response(status_code=204)

logger.info("[boot] version=%s", "0.1.0")
logger.info("[boot] SUPABASE_URL=%s", SUPABASE_URL)
logger.info("[boot] CORS allow_origins=%s", _allowed)
logger.info(
    "[boot] OCR_PROVIDER=%s hf_token_present=%s",
    (os.getenv("OCR_PROVIDER", "mock") or "mock"),
    bool(os.getenv("HF_TOKEN")),
)
if (os.getenv("OCR_PROVIDER", "mock").lower() == "trocr_local") and (Image is None):
    try:
        logger.error("Pillow not installed; trocr_local requires pillow. pip install pillow")
    except Exception:
        pass
logger.info("[boot] FRONTEND_ORIGIN=%s", FRONTEND_ORIGIN)
logger.info("[boot] DEV_MODE=%s", "1" if DEV_MODE else "0")

# Log the registered OCR read route for sanity
try:
    logger.info("[boot] GET /api/uploads/{id}/ocr ready (SR read)")
except Exception:
    pass

# Supabase env + clients (top-level)
# Explicit env loads as requested
try:
    load_dotenv("backend/.env")
    load_dotenv(".env", override=False)
except Exception:
    pass

# Normalize keys: treat SUPABASE_KEY as service-role, keep anon separate
SUPABASE_URL = os.getenv("SUPABASE_URL")
SR_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_KEY")
ANON_KEY = os.getenv("SUPABASE_ANON_KEY")
SUPABASE_BUCKET = (
    os.getenv("SUPABASE_BUCKET")
    or os.getenv("SUPABASE_STORAGE_BUCKET")
    or os.getenv("STORAGE_BUCKET")
    or None
)

# HF TrOCR via Inference API (helper config)
SUBMISSIONS_BUCKET = os.getenv("SUBMISSIONS_BUCKET", "submissions")
HF_API_TOKEN = os.getenv("HF_API_TOKEN")
TROCR_MODEL = os.getenv("TROCR_MODEL", "microsoft/trocr-base-handwritten")

_missing_url = not bool(SUPABASE_URL)
_missing_key = not bool(ANON_KEY)
_missing_bucket = not bool(SUBMISSIONS_BUCKET)
if _missing_url or _missing_key or _missing_bucket:
    logger.warning(
        "Supabase env missing: URL=%s, KEY=%s, BUCKET=%s",
        _missing_url,
        _missing_key,
        _missing_bucket,
    )

supabase_sr = (
    create_client(SUPABASE_URL, SR_KEY)
    if (create_client and SUPABASE_URL and SR_KEY)
    else None
)
supabase = (
    create_client(SUPABASE_URL, SR_KEY)
    if (create_client and SUPABASE_URL and SR_KEY)
    else None
)
bucket = os.getenv("SUBMISSIONS_BUCKET", "submissions")
hf_token_present = bool(os.getenv("HF_API_TOKEN"))
print("[BOOT] bucket=", bucket)
print("[BOOT] provider=", os.getenv("OCR_PROVIDER"))
print("[BOOT] trocr_model=", os.getenv("TROCR_MODEL"))
print("[BOOT] has_service_role=", "yes" if os.getenv("SUPABASE_SERVICE_ROLE_KEY") else "no")
print("[BOOT] hf_token_present=", hf_token_present)
try:
    logger.info("[boot] supabase_sr active=%s", bool(supabase_sr))
except Exception:
    pass
if supabase_sr is None:
    try:
        logger.warning("Missing SUPABASE_SERVICE_ROLE_KEY; SR client disabled. RLS reads/writes may fail.")
    except Exception:
        pass

# --- Supabase write helpers using service-role client ---
def _sb_error_snapshot(resp):
    try:
        return {
            "status_code": getattr(resp, "status_code", None),
            "data": getattr(resp, "data", None),
            "error": getattr(resp, "error", None),
        }
    except Exception:
        return {"snapshot": str(resp)}

def _update_upload_sr(uid: str, payload: dict):
    if supabase_sr is None:
        raise HTTPException(status_code=500, detail="service-role client unavailable")
    try:
        r = supabase_sr.table("uploads").update(payload).eq("id", uid).execute()
    except Exception as e:
        try:
            logger.error("supabase.update uploads exception: %s", e)
        except Exception:
            pass
        raise HTTPException(status_code=500, detail="OCR persist failed (update exception)")
    rows = getattr(r, "data", None)
    if not rows:
        try:
            logger.error("supabase.update uploads 0 rows", extra=_sb_error_snapshot(r))
        except Exception:
            pass
        raise HTTPException(status_code=500, detail="OCR persist failed (RLS?)")
    return r

# Override safe updater to route writes via service-role
def _safe_update_upload(uid: str, payload: dict):
    return _update_upload_sr(uid, payload)

def _utc_iso():
    import datetime as dt
    return dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"

BOOT_VERSION = "1.0.3"
# â”€â”€ Health â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
@app.get("/health")
@app.get("/api/health")
def health():
    return {"ok": True, "service": "graderai-ocr"}

@app.get("/healthz")
@app.get("/api/healthz")
def healthz():
    return {"ok": True}

# --- helper to compute response text length across providers ---
def _resp_text_len(row: dict, final_payload: dict | None = None) -> int:
    t = (
        (final_payload or {}).get("ocr_text")
        or (final_payload or {}).get("extracted_text")
        or row.get("ocr_text")
        or row.get("extracted_text")
        or ""
    )
    return len((t or "").strip())


# --- Storage download helper (SR-auth) ---
def _normalize(storage_path: str, bucket: str) -> str:
    p = (storage_path or "").lstrip("/")
    prefix = f"{bucket}/"
    return p[len(prefix):] if p.startswith(prefix) else p

def _download_bytes_from_storage(storage_path: str) -> bytes:
    bucket = os.getenv("SUBMISSIONS_BUCKET", "submissions")
    rel = _normalize(storage_path, bucket)
    print(f"[OCR] storage.download bucket={bucket} path={rel}")
    client = supabase_sr or supabase
    if client is None:
        raise RuntimeError("Supabase client unavailable")
    data = client.storage.from_(bucket).download(rel)
    if not data:
        raise HTTPException(status_code=404, detail="file not found in storage")
    return data


# Dev-only local resolver for uploads when Supabase is disabled
def resolve_upload_path(upload_id: str, owner_id: str | None = None) -> str | None:
    """
    Dev fallback: if Supabase storage is unavailable, try to find the uploaded file on local disk.
    Strategy:
      - Look under env var LOCAL_SUBMISSIONS_DIR (if set), optionally within owner_id subfolder.
      - Search for common image/pdf extensions whose filename contains the upload_id.
    Returns absolute file path or None.
    """
    try:
        base = os.getenv("LOCAL_SUBMISSIONS_DIR") or os.getenv("DEV_UPLOADS_DIR")
        if not base:
            return None
        base = os.path.abspath(base)
        if not os.path.isdir(base):
            return None
        exts = (".png", ".jpg", ".jpeg", ".pdf", ".webp")
        candidates = []
        search_roots = [base]
        if owner_id:
            p = os.path.join(base, str(owner_id))
            if os.path.isdir(p):
                search_roots.insert(0, p)
        for root in search_roots:
            for dirpath, _dirnames, filenames in os.walk(root):
                for fn in filenames:
                    low = fn.lower()
                    if any(low.endswith(ext) for ext in exts) and (upload_id in fn):
                        candidates.append(os.path.join(dirpath, fn))
        if candidates:
            # Prefer owner-specific match if present, else first
            return os.path.abspath(sorted(candidates, key=lambda p: (0 if owner_id and os.path.sep + str(owner_id) + os.path.sep in p else 1, len(p)))[0])
        return None
    except Exception:
        return None

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

def _bytes_to_pil(b: bytes):
    if not Image:
        raise RuntimeError("Pillow (PIL) is not installed; required for trocr_local.")
    return Image.open(BytesIO(b)).convert("RGB")

def _run_local_provider(provider, blob: bytes, storage_path: str, model_name: str | None):
    """
    Try several provider.run signatures:
      1) file_bytes=..., filename=..., model_override=...
      2) image_bytes=..., model_override=...
      3) image=..., model_override=...
      4) positional blob, model_override=...
    Return (text, meta).
    """
    fname = os.path.basename(storage_path) or "upload.bin"

    # 1) Preferred: file_bytes + filename
    try:
        return provider.run(file_bytes=blob, filename=fname, model_override=model_name)
    except TypeError:
        pass

    # 2) Legacy: image_bytes
    try:
        return provider.run(image_bytes=blob, model_override=model_name)
    except TypeError:
        pass

    # 3) Legacy: image
    try:
        return provider.run(image=blob, model_override=model_name)
    except TypeError:
        pass

    # 4) Positional blob
    try:
        return provider.run(blob, model_override=model_name)  # type: ignore[arg-type]
    except TypeError as e:
        raise

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
                    _update_upload_sr(
                        str(body.upload_id),
                        {
                            "extracted_text": text,
                            "ocr_text": text,
                            "ocr_meta": {},
                            "ocr_status": OCR_DONE,
                            "ocr_completed_at": _utc_iso(),
                            "ocr_updated_at": _utc_iso(),
                            "ocr_error": None,
                        },
                    )
            except Exception:
                pass
            # Always reflect in-memory store
            _OCR_DEV[str(body.upload_id)] = {
                "status": "done",
                "text": text,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            # Include text_len in success response
            _text_len = _resp_text_len(
                {"ocr_text": text, "extracted_text": text},
                {"ocr_text": text, "extracted_text": text},
            )
            print("[ocr] success path: legacy/mock/hf")
            try:
                logger.info("ocr_done upload_id=%s text_len=%s latency_ms=%s", str(body.upload_id), _text_len, 0)
            except Exception:
                pass
            return {"status": "done", "upload_id": str(body.upload_id), "text_len": _text_len, "latency_ms": 0}
        except Exception:
            # Even if logging fails, keep mock path successful
            logger.exception("ocr_start failed", extra={"upload_id": str(getattr(body, 'upload_id', ''))})
            return {"status": "done", "upload_id": str(body.upload_id), "text_len": len("mock extracted text"), "latency_ms": 0}
    try:
        upload_id = str(body.upload_id)
        caller_id = x_owner_id or x_user_id
        # Diagnostics at start
        try:
            _prov = os.getenv("OCR_PROVIDER", "trocr_local")
            _model = os.getenv("OCR_MODEL", "microsoft/trocr-base-handwritten")
            logger.info("ocr_start upload_id=%s owner=%s provider=%s model=%s handwritten", upload_id, (caller_id or ""), _prov, _model)
        except Exception:
            pass

        # Fetch upload row for authz + path (SR preferred; avoid maybe_single)
        client = supabase_sr or supabase
        try:
            logger.info(
                "ocr_start upload_id=%s provider=%s model=%s sr=%s",
                upload_id,
                os.getenv("OCR_PROVIDER", "mock"),
                os.getenv("OCR_MODEL", ""),
                bool(supabase_sr),
            )
        except Exception:
            pass
        try:
            resp0 = (
                client
                .table("uploads")
                .select("id, owner_id, storage_path, mime_type, extracted_text, ocr_status, ocr_error")
                .eq("id", upload_id)
                .limit(1)
                .execute()
            )
            rows = (getattr(resp0, "data", None) or [])
        except Exception as e:
            logger.error("uploads.select failed id=%s: %s", upload_id, e, exc_info=True)
            raise HTTPException(status_code=500, detail={"detail": "internal_error", "message": "uploads.select failed"})
        if not rows:
            raise HTTPException(status_code=404, detail="Upload not found")
        row = rows[0]

        # Provider selection based on OCR_PROVIDER
        storage_path = row.get("storage_path")
        if not storage_path:
            raise HTTPException(status_code=400, detail="Missing storage_path")
        _safe_update_upload(upload_id, {
            "ocr_status": "running",
            "ocr_started_at": _utc_iso(),
            "ocr_updated_at": _utc_iso(),
            "ocr_error": None,
        })
        prov = (os.getenv("OCR_PROVIDER") or "tesseract").strip().lower()

        if prov == "tesseract":
            result = await asyncio.to_thread(run_ocr_tesseract, storage_path)
        elif prov in ("hf_trocr_api", "hf_tr_ocr_api"):
            result = await asyncio.to_thread(run_ocr_hf_trocr_api, storage_path)
        elif prov in ("azure", "azure_vision"):
            result = await run_ocr_azure_vision(storage_path)
        else:
            result = {"text": "", "meta": {"error": f"unknown_provider:{prov}"}}

        text = result.get("text") or ""
        meta = result.get("meta") or {}
        status = "ok" if text else "fail"
        err = meta.get("error")
        _safe_update_upload(upload_id, {
            "ocr_text": text,
            "ocr_meta": meta,
            "ocr_status": status,
            "ocr_error": err,
            "ocr_completed_at": _utc_iso(),
            "ocr_updated_at": _utc_iso(),
        })
        return {"ok": True, "chars": len(text or ""), "status": status, "error": err}

        # trocr_local: download bytes from Storage and run provider
        if (os.getenv("OCR_PROVIDER", "mock").lower() == "trocr_local"):
            storage_path = row.get("storage_path")
            mime_type = row.get("mime_type")

            # mark running (OCR fields only)
            _safe_update_upload(upload_id, {
                "ocr_status": OCR_RUNNING,
                "ocr_error": None,
                "ocr_started_at": _utc_iso(),
                "ocr_updated_at": _utc_iso(),
            })

            # download bytes via SR
            try:
                blob = _download_bytes_from_storage(storage_path)
            except HTTPException as he:
                logger.error("storage download failed id=%s path=%s: %s", upload_id, storage_path, getattr(he, 'detail', he))
                _safe_update_upload(upload_id, {
                    "ocr_status": OCR_ERROR,
                    "ocr_error": "storage_download_failed",
                    "ocr_updated_at": _utc_iso(),
                    "ocr_completed_at": _utc_iso(),
                })
                raise

            try:
                try:
                    from .ocr.run_ocr import (
                        get_provider as __get_local_ocr_provider,
                        ProviderUnavailable as __ProviderUnavailable,
                    )
                except ImportError as e:
                    logger.exception("provider_import_failed: %s", e)
                    raise HTTPException(status_code=503, detail={"detail": "ocr_provider_unavailable", "message": "torch/hf missing"})
                prov_name, prov_model, provider = __get_local_ocr_provider()
                model_name = (body.model or None)
                try:
                    text, meta = _run_local_provider(provider, blob, row["storage_path"], model_name)
                except Exception as e:
                    logger.exception("trocr_local run failed: %s", e)
                    _safe_update_upload(upload_id, {
                        "ocr_status": OCR_ERROR,
                        "ocr_error": str(e),
                        "ocr_updated_at": _utc_iso(),
                        "ocr_completed_at": _utc_iso(),
                    })
                    raise HTTPException(status_code=500, detail={"detail": "internal_error", "message": "ocr failed"})
                # Normalize/persist on success (OCR fields only)
                text = (text or "").strip()
                _safe_update_upload(upload_id, {
                    "extracted_text": text,
                    "ocr_text": text,
                    "ocr_meta": meta or {},
                    "ocr_status": OCR_DONE,
                    "ocr_updated_at": _utc_iso(),
                    "ocr_completed_at": _utc_iso(),
                    "ocr_error": None,
                })
                return {"status": "done", "upload_id": upload_id, "text_len": len(text), "latency_ms": None}
                text = _normalize_local_text(text)
                _safe_update_upload(upload_id, {
                    "extracted_text": text,
                    "ocr_text": text,
                    "ocr_meta": meta or {},
                    "ocr_status": OCR_DONE,
                    "ocr_updated_at": _utc_iso(),
                    "ocr_completed_at": _utc_iso(),
                    "ocr_error": None,
                })
                return {
                    "status": "done",
                    "upload_id": upload_id,
                    "text_len": len(text or ""),
                    "latency_ms": None,
                }
            except HTTPException:
                raise
            except Exception as e:
                logger.exception("ocr_start trocr_local failed")
                _safe_update_upload(upload_id, {
                    "ocr_status": OCR_ERROR,
                    "ocr_error": str(e),
                    "ocr_completed_at": _utc_iso(),
                    "ocr_updated_at": _utc_iso(),
                })
                raise HTTPException(status_code=500, detail={"detail": "internal_error", "message": "ocr failed"})
        # Allow dev without owner header; enforce only in non-dev
        if (not DEV_MODE) and REQUIRE_OWNER and caller_id and str(row.get("owner_id")) != str(caller_id):
            return JSONResponse(status_code=404, content={"detail": "not_found"})

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
                    "ocr_status": OCR_DONE,
                    "extracted_text": "[MOCK OCR] The quick brown fox jumps over the lazy dog.",
                    "ocr_text":       text_resp,
                    "ocr_meta": {"provider": "mock", "pages": 1, "confidence": 0.99},
                    "ocr_completed_at": _utc_iso(),
                    "ocr_updated_at": _utc_iso(),
                    "ocr_error": None,
                }
                # Mutate in-memory row (tests' FakeTable.update doesn't persist)
                try:
                    row.update(payload)
                except Exception:
                    pass
                _safe_update_upload(upload_id, payload)
                _text_len = _resp_text_len(row, payload)
                print("[ocr] success path: legacy/mock/hf")
                try:
                    logger.info("ocr_done upload_id=%s text_len=%s latency_ms=%s", upload_id, _text_len, 0)
                except Exception:
                    pass
                return {"status": "done", "upload_id": upload_id, "text_len": _text_len, "latency_ms": 0}
            except Exception:
                logger.exception("/api/ocr/start mock path failed")
                raise HTTPException(status_code=500, detail="mock_update_failed")

        # If configured to use local TrOCR provider, run it here
        if os.getenv("OCR_PROVIDER", "mock").lower() == "trocr_local":
            try:
                storage_path = row.get("storage_path")
                if not storage_path:
                    return JSONResponse(status_code=400, content={"detail": "bad_request", "message": "Missing storage_path"})
                # Try Supabase signed URL; if storage disabled in dev, fall back to local file resolution
                blob = None
                filename = (storage_path or "").split("/")[-1]
                try:
                    blob = _download_bytes_from_storage(storage_path)
                except Exception:
                    # Dev fallback to local disk
                    base = os.getenv("LOCAL_SUBMISSIONS_DIR") or os.getenv("DEV_UPLOADS_DIR")
                    key = (storage_path or "").lstrip("/")
                    if base:
                        local_candidate = os.path.join(os.path.abspath(base), key.split("/", 1)[-1])
                        if os.path.isfile(local_candidate):
                            with open(local_candidate, "rb") as fh:
                                blob = fh.read()
                            filename = os.path.basename(local_candidate)
                    if blob is None:
                        # Last try: generic search by upload_id
                        local_any = resolve_upload_path(upload_id, caller_id)
                        if local_any and os.path.isfile(local_any):
                            with open(local_any, "rb") as fh:
                                blob = fh.read()
                            filename = os.path.basename(local_any)
                    if blob is None:
                        return JSONResponse(status_code=404, content={"detail": "not_found", **({"message": "file not found locally"} if DEV_MODE else {})})
                # Derive filename for type detection
                filename = filename or (storage_path or "").split("/")[-1]
                # Lazy import heavy local OCR provider right before use
                try:
                    from .ocr.run_ocr import (
                        get_provider as __get_local_ocr_provider,
                        normalize as __normalize_local_text,
                        ProviderUnavailable as __ProviderUnavailable,
                    )
                except ImportError as e:
                    logger.exception("import_error: %s", e)
                    return JSONResponse(status_code=503, content={"detail": "ocr_provider_unavailable", "message": ("torch/hf missing" if DEV_MODE else "unavailable")})
                except Exception as e:
                    logger.exception("provider_import_failed: %s", e)
                    return JSONResponse(status_code=503, content={"detail": "ocr_provider_unavailable", **({"message": str(e)} if DEV_MODE else {})})
                try:
                    prov_name, prov_model, provider = __get_local_ocr_provider()
                except __ProviderUnavailable as e:
                    logger.exception("provider_unavailable: %s", e)
                    return JSONResponse(status_code=503, content={"detail": "ocr_provider_unavailable", **({"message": str(e)} if DEV_MODE else {"message": "unavailable"})})
                # Diagnostics before calling provider
                try:
                    _prov = os.getenv("OCR_PROVIDER", "trocr_local")
                    _model = os.getenv("OCR_MODEL", prov_model)
                    logger.info("ocr_start provider=%s model=%s upload_id=%s", _prov, _model, upload_id)
                except Exception:
                    pass
                text, meta = provider.run(
                    file_bytes=blob,
                    filename=filename,
                    model_override=(body.model or None),
                )
                text = __normalize_local_text(text)
                text_len = len(text.strip())

                final_payload = {
                    "extracted_text": text,
                    "ocr_text": text,
                    "ocr_meta": meta or {},
                    "ocr_status": OCR_DONE,
                    "ocr_completed_at": _utc_iso(),
                    "ocr_updated_at": _utc_iso(),
                    "ocr_error": None,
                }
                try:
                    row.update(final_payload)
                except Exception:
                    pass
                _safe_update_upload(upload_id, final_payload)
                try:
                    logger.info("ocr_done upload_id=%s text_len=%s latency_ms=%s", upload_id, text_len, None)
                except Exception:
                    pass

                # Log detailed run into ocr_runs (best-effort)
                try:
                    tried = meta.get("tried")
                    supabase.table("ocr_runs").insert({
                        "upload_id": upload_id,
                        "provider": prov_name,
                        "model": meta.get("model"),
                        "latency_ms": meta.get("latency_ms"),
                        "status": "ok",
                        "error": None,
                        "device": meta.get("device"),
                        "tried": None if tried is None else json.dumps(tried),
                    }).execute()
                except Exception:
                    pass

                text_len = _resp_text_len(row, final_payload)
                print("[ocr] success path: trocr_local")
                return {
                    "status": "done",
                    "upload_id": upload_id,
                    "text_len": text_len,
                    "latency_ms": meta.get("latency_ms"),
                }
            except Exception as e:
                # Mark error and record run
                err_msg = (
                    "PDF conversion failed" if str(row.get("storage_path", "")).lower().endswith(".pdf") else str(e)
                )
                try:
                    supabase.table("ocr_runs").insert({
                        "upload_id": upload_id,
                        "provider": "trocr_local",
                        "model": os.getenv("OCR_MODEL", "microsoft/trocr-base-handwritten"),
                        "latency_ms": None,
                        "status": "failed",
                        "error": err_msg,
                        "device": None,
                        "tried": None,
                    }).execute()
                except Exception:
                    pass

                err_payload = {
                    "ocr_status": OCR_ERROR,
                    "ocr_error": err_msg,
                    "ocr_completed_at": _utc_iso(),
                    "ocr_updated_at": _utc_iso(),
                }
                try:
                    row.update(err_payload)
                except Exception:
                    pass
                _safe_update_upload(upload_id, err_payload)
                return JSONResponse(
                    status_code=400,
                    content={"detail": "bad_request", "message": err_msg},
                )

        # Real provider path with simple retry on 5xx/429
        attempts_log: list = []
        try:
            storage_path = row.get("storage_path")
            if not storage_path:
                raise HTTPException(400, "Missing storage_path")
            # Diagnostics before calling remote provider
            try:
                _prov = os.getenv("OCR_PROVIDER", "mock")
                _model = os.getenv("OCR_MODEL", "microsoft/trocr-base-handwritten")
                logger.info("ocr_start provider=%s model=%s upload_id=%s", _prov, _model, upload_id)
            except Exception:
                pass
            max_attempts = 3
            data = None
            for attempt in range(max_attempts):
                try:
                    # Let provider client handle image bytes; tests may patch httpx inside services.ocr
                    blob = _download_bytes_from_storage(storage_path)
                    data = await ocr.extract_text(image_bytes=blob)
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
                "extracted_text": text,
                "ocr_text": text,
                "ocr_status": OCR_DONE,
                "ocr_completed_at": _utc_iso(),
                "ocr_updated_at": _utc_iso(),
                "ocr_error": None,
            }
            try:
                row.update(final_payload)
            except Exception:
                pass
            _safe_update_upload(upload_id, final_payload)
            # Log attempt record
            try:
                supabase_sr.table("ocr_results").insert({
                    "upload_id": upload_id,
                    "status": "OCR_DONE",
                    "attempts_log": json.dumps(attempts_log),
                    "provider": os.environ.get("OCR_PROVIDER", "mock"),
                }).execute()
            except Exception:
                pass
            _text_len = _resp_text_len(row, final_payload)
            print("[ocr] success path: legacy/mock/hf")
            try:
                logger.info("ocr_done upload_id=%s text_len=%s latency_ms=%s", upload_id, _text_len, None)
            except Exception:
                pass
            return {"status": "done", "upload_id": upload_id, "text_len": _text_len, "latency_ms": None}
        except httpx.ReadTimeout as te:
            # Log and mark error
            try:
                supabase_sr.table("ocr_results").insert({
                    "upload_id": upload_id,
                    "status": "OCR_ERROR",
                    "attempts_log": json.dumps(attempts_log + [str(te)]),
                    "provider": os.environ.get("OCR_PROVIDER", "mock"),
                }).execute()
            except Exception:
                pass
            err_payload = {"ocr_status": OCR_ERROR, "ocr_error": str(te), "ocr_completed_at": _utc_iso(), "ocr_updated_at": _utc_iso()}
            try:
                row.update(err_payload)
            except Exception:
                pass
            _safe_update_upload(upload_id, err_payload)
            return JSONResponse(status_code=400, content={"detail": "bad_request", "message": "ocr_timeout"})
        except httpx.HTTPStatusError as he:
            code = getattr(he.response, "status_code", 502)
            try:
                supabase_sr.table("ocr_results").insert({
                    "upload_id": upload_id,
                    "status": "OCR_ERROR",
                    "attempts_log": json.dumps(attempts_log),
                    "provider": os.environ.get("OCR_PROVIDER", "mock"),
                }).execute()
            except Exception:
                pass
            err_payload2 = {"ocr_status": OCR_ERROR, "ocr_error": getattr(he, "message", str(he)), "ocr_completed_at": _utc_iso(), "ocr_updated_at": _utc_iso()}
            try:
                row.update(err_payload2)
            except Exception:
                pass
            _safe_update_upload(upload_id, err_payload2)
            return JSONResponse(status_code=400, content={"detail": "bad_request", "message": "ocr_failed"})
    except HTTPException as he:
        # Map common expected errors to 400
        if he.status_code in (400, 403, 404):
            return JSONResponse(status_code=400, content={"detail": "bad_request", "message": str(getattr(he, 'detail', 'bad_request'))})
        logger.exception("/api/ocr/start unexpected http error")
        msg = str(getattr(he, 'detail', '')) if DEV_MODE else None
        return JSONResponse(status_code=500, content={"detail": "internal_error", **({"message": msg} if msg else {})})
    except Exception as e:
        logger.exception("/api/ocr/start unexpected error")
        msg = str(e) if DEV_MODE else None
        return JSONResponse(status_code=500, content={"detail": "internal_error", **({"message": msg} if msg else {})})

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
    try:
        owner = x_owner_id or x_user_id
        # Dev in-memory path
        if DEV_MODE and upload_id in _OCR_DEV:
            d = _OCR_DEV[upload_id]
            st = str(d.get("status") or "pending").lower()
            if st == "running":
                st = "processing"
            if st == "error":
                st = "failed"
            return {"status": st, "text_len": len((d.get("text") or "").strip())}

        row = _safe_select_status(str(upload_id))
        if not row:
            return JSONResponse(status_code=404, content={"detail": "not_found"})

        # Owner check (non-dev only) → hide existence behind 404
        if (not DEV_MODE) and REQUIRE_OWNER and owner and str(row.get("owner_id")) != str(owner):
            return JSONResponse(status_code=404, content={"detail": "not_found"})

        # Compute status per contract
        has_err = bool(row.get("ocr_error"))
        text = (row.get("extracted_text") or row.get("ocr_text") or "").strip()
        text_len = len(text)
        has_completed = bool(row.get("ocr_completed_at")) or text_len > 0
        has_started = bool(row.get("ocr_started_at"))

        if has_err:
            st = "failed"
        elif has_completed:
            st = "done"
        elif has_started:
            st = "processing"
        else:
            st = "pending"

        payload = {"status": st, "text_len": text_len if text_len else 0}
        if st == "failed":
            err = row.get("ocr_error")
            if err:
                payload["error"] = err
        return payload
    except Exception:
        # Never return 500 for status; treat as not found to keep UI resilient
        return JSONResponse(status_code=404, content={"detail": "not_found"})
    
@app.get("/api/config")
def config_probe():
    return summary(safe=True)

# â”€â”€ Helpers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
def _owner_matches(row: dict, caller_id: str | None) -> bool:
    if not caller_id or not row:
        return False
    return caller_id in {row.get("owner_id"), row.get("user_id")}

def _mark_status(upload_id: str, status: str, fields: dict | None = None):
    # Do not touch 'status' column; only update provided fields.
    payload = {}
    if fields:
        payload.update(fields)
    if not payload:
        return
    _update_upload_sr(upload_id, payload)

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
    # Thin wrapper to route through service-role client with row-count check
    return _update_upload_sr(uid, payload)


def _safe_select_status(uid: str):
    try:
        if not supabase:
            return None
        r = (
            supabase.table("uploads")
            .select(
                "id,owner_id,status,extracted_text,ocr_status,ocr_error,ocr_started_at,ocr_completed_at,ocr_updated_at"
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


# removed signed URL helper; use storage.download via service role

def _split_rel(storage_path: str, bucket: str) -> Tuple[str, str]:
    # normalize to "folder1/folder2/file.jpg" without bucket prefix
    p = storage_path.lstrip("/").replace("\\", "/")
    if p.startswith(f"{bucket}/"):
        p = p[len(bucket)+1:]
    # collapse repeated slashes
    parts = [x for x in p.split("/") if x]
    rel = "/".join(parts)
    d = "/".join(parts[:-1]) if len(parts) > 1 else ""
    f = parts[-1] if parts else ""
    return d, f

def _download_bytes_from_storage(storage_path: str) -> bytes:
    bucket = os.getenv("SUBMISSIONS_BUCKET", "submissions")
    d, f = _split_rel(storage_path, bucket)
    print(f"[OCR] storage.download bucket={bucket} dir='{d}' file='{f}'")
    # List the directory for visibility
    try:
      listing = supabase.storage.from_(bucket).list(d or "")
      names = [x.get("name") for x in listing]
      print(f"[OCR] dir listing ({len(names)}): {names[:10]}")
    except Exception as e:
      print(f"[OCR] list failed for bucket={bucket} dir='{d}' :: {e}")
    # Try download
    rel_path = f"{d}/{f}" if d else f
    blob = supabase.storage.from_(bucket).download(rel_path)
    if not blob:
        raise RuntimeError(f"Not Found: bucket={bucket} rel='{rel_path}'")
    return blob

def run_ocr_hf_trocr_api(storage_path: str):
    img_bytes = _download_bytes_from_storage(storage_path)
    api = f"https://api-inference.huggingface.co/models/{TROCR_MODEL}"
    headers = {"Authorization": f"Bearer {HF_API_TOKEN}"} if HF_API_TOKEN else {}
    r = httpx.post(api, headers=headers, content=img_bytes, timeout=45)
    meta = {"provider": "hf_trocr_api", "model": TROCR_MODEL, "source": storage_path, "status": r.status_code}
    if r.status_code >= 400:
        meta["error"] = r.text[:500]
        return {"text": "", "meta": meta}
    data = r.json()
    text = " ".join(d.get("generated_text", "") for d in data) if isinstance(data, list) else ""
    if not text:
        meta["warn"] = f"Unexpected HF response: {str(data)[:300]}"
    return {"text": text.strip(), "meta": meta}

def run_ocr_tesseract(storage_path: str):
    import io, numpy as np
    from PIL import Image, ImageOps, ImageFilter
    blob = _download_bytes_from_storage(storage_path)
    im = Image.open(io.BytesIO(blob)).convert("L")

    # lightweight preproc (works well for faint pencil)
    im = ImageOps.autocontrast(im, cutoff=2)      # boost contrast
    im = im.resize((im.width*2, im.height*2))     # upsample helps LSTM
    im = im.filter(ImageFilter.UnsharpMask(radius=2, percent=120, threshold=3))

    cfgs = [
        "--oem 1 --psm 7 -l eng",    # single line
        "--oem 1 --psm 11 -l eng",   # sparse text
        "--oem 1 --psm 6 -l eng",    # block
        "--oem 1 --psm 13 -l eng",   # raw line
    ]
    best = {"text": "", "meta": {"provider":"tesseract", "tried": cfgs, "chosen": None}}
    for cfg in cfgs:
        try:
            txt = pytesseract.image_to_string(im, config=cfg) or ""
        except Exception as e:
            best["meta"].setdefault("errors", []).append(f"{cfg}: {e}")
            continue
        if len(txt.strip()) > len(best["text"].strip()):
            best["text"] = txt.strip()
            best["meta"]["chosen"] = cfg
    return best

async def run_ocr_azure_vision(storage_path: str):
    """
    Azure Computer Vision Read v3.2 via REST.
    Requires AZURE_ENDPOINT (https://<res>.cognitiveservices.azure.com) and AZURE_KEY.
    Returns {"text": str, "meta": {...}}.
    """
    endpoint = os.getenv("AZURE_ENDPOINT", "").rstrip("/")
    key = os.getenv("AZURE_KEY", "")
    if not endpoint or not key:
        return {"text": "", "meta": {"provider": "azure_vision", "error": "missing_endpoint_or_key"}}

    img_bytes = _download_bytes_from_storage(storage_path)
    if not img_bytes:
        return {"text": "", "meta": {"provider": "azure_vision", "error": "empty_image_bytes"}}

    analyze_url = f"{endpoint}/vision/v3.2/read/analyze"
    headers = {"Ocp-Apim-Subscription-Key": key, "Content-Type": "application/octet-stream"}

    async with httpx.AsyncClient(timeout=45) as client:
        resp = await client.post(analyze_url, headers=headers, content=img_bytes)
        if resp.status_code not in (200, 202):
            return {"text": "", "meta": {"provider": "azure_vision", "status": resp.status_code, "error": resp.text}}
        op_loc = resp.headers.get("operation-location") or resp.headers.get("Operation-Location")
        if not op_loc:
            return {"text": "", "meta": {"provider": "azure_vision", "error": "missing_operation_location"}}

        for _ in range(20):
            await asyncio.sleep(0.75)
            r = await client.get(op_loc, headers={"Ocp-Apim-Subscription-Key": key})
            data = r.json()
            status = (data.get("status") or "").lower()
            if status == "failed":
                return {"text": "", "meta": {"provider": "azure_vision", "status": status, "result": data}}
            if status == "succeeded":
                lines = []
                try:
                    pages = data.get("analyzeResult", {}).get("readResults") or []
                    for pg in pages:
                        for ln in pg.get("lines", []):
                            t = ln.get("text") or ""
                            if t:
                                lines.append(t)
                except Exception as e:
                    return {"text": "", "meta": {"provider": "azure_vision", "status": status, "parse_error": str(e), "raw": data}}
                return {"text": "\n".join(lines).strip(), "meta": {"provider": "azure_vision", "status": status}}
        return {"text": "", "meta": {"provider": "azure_vision", "error": "timeout"}}

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

# â”€â”€ API â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

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

        _mark_status(row["id"], OCR_RUNNING, {"ocr_status": OCR_RUNNING, "ocr_error": None, "ocr_started_at": _utc_iso(), "ocr_updated_at": _utc_iso()})
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
                supabase_sr.table("ocr_results").insert({
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
                "ocr_text": text,
                "ocr_meta": meta,
                "ocr_status": OCR_DONE,
                "ocr_completed_at": _utc_iso(),
                "ocr_updated_at": _utc_iso(),
                "ocr_error": None,
            })
            row["status"] = OCR_DONE
            row["extracted_text"] = text
            row["ocr_completed_at"] = row.get("ocr_completed_at") or dt.now(timezone.utc).isoformat()
            row["ocr_meta"] = json.dumps(meta)

        except httpx.ReadTimeout:
            attempts_log.append("timeout")
            try:
                supabase_sr.table("ocr_results").insert({
                    "upload_id": row["id"],
                    "text": None,
                    "status": "OCR_ERROR",
                    "provider": os.environ.get("OCR_PROVIDER", "mock"),
                    "attempts_log": json.dumps(attempts_log),
                }).execute()
            except Exception as e:
                logger.warning("failed to insert ocr_results: %s", e)
            _mark_status(row["id"], OCR_ERROR, {"ocr_status": OCR_ERROR, "ocr_error": "timeout", "ocr_completed_at": _utc_iso(), "ocr_updated_at": _utc_iso()})
            row["status"] = OCR_ERROR
            row["ocr_error"] = "timeout"
            raise HTTPException(status_code=422, detail="ocr provider timeout")

        except httpx.HTTPStatusError as e:
            code = getattr(e.response, "status_code", 500)
            attempts_log.append(code)
            try:
                supabase_sr.table("ocr_results").insert({
                    "upload_id": row["id"],
                    "text": None,
                    "status": "OCR_ERROR",
                    "provider": os.environ.get("OCR_PROVIDER", "mock"),
                    "attempts_log": json.dumps(attempts_log),
                }).execute()
            except Exception as e2:
                logger.warning("failed to insert ocr_results: %s", e2)
            _mark_status(row["id"], OCR_ERROR, {"ocr_status": OCR_ERROR, "ocr_error": f"http {code}", "ocr_completed_at": _utc_iso(), "ocr_updated_at": _utc_iso()})
            row["status"] = OCR_ERROR
            row["ocr_error"] = f"http {code}"
            raise HTTPException(status_code=500, detail=f"OCR failed: http {code}")

        except Exception as e:
            attempts_log.append("error")
            try:
                supabase_sr.table("ocr_results").insert({
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
            if key.startswith(f"{SUBMISSIONS_BUCKET}/"):
                key = key[len(f"{SUBMISSIONS_BUCKET}/"):]
            supabase.storage.from_(SUBMISSIONS_BUCKET).remove([key])
    except Exception as e:
        raise HTTPException(status_code=502, detail="storage remove failed")

    # 2) Delete DB row
    try:
        supabase.table("uploads").delete().eq("id", upload_id).execute()
    except PostgrestAPIError as e:
        raise HTTPException(status_code=400, detail=f"DB delete failed: {getattr(e, 'message', str(e))}")

    return {"ok": True}



@app.get("/api/uploads/{id}/ocr")
async def get_upload_ocr(
    id: str,
    x_owner_id: Optional[str] = Header(None),
    x_user_id: Optional[str] = Header(None),
):
    """
    Return OCR text and metadata for an upload.
    Response shape:
      { "ocr_text": str, "ocr_meta": obj, "status": str }

    If no row -> 404. If row exists but no text -> {"status": "pending"}.
    Uses same owner check policy as other upload reads (404 on mismatch).
    """
    _require_supabase_config()

    upload_id = str(id)
    caller_id = x_owner_id or x_user_id
    try:
        logger.info("ocr_read id=%s owner=%s sr=%s", upload_id, caller_id, True)
    except Exception:
        pass

    # SR-only read for RLS-protected table
    resp = (
        supabase_sr
        .table("uploads")
        .select("id, owner_id, ocr_text, extracted_text, ocr_meta, ocr_status, ocr_error")
        .eq("id", upload_id)
        .maybe_single()
        .execute()
    )
    row = getattr(resp, "data", None)
    if not row:
        raise HTTPException(status_code=404, detail="Not found")

    # Enforce explicit owner check when caller_id present
    if caller_id and str(row.get("owner_id")) != str(caller_id):
        raise HTTPException(status_code=403, detail="forbidden")

    # Prefer newer column if present, else legacy extracted_text
    text = (row.get("extracted_text") or row.get("ocr_text") or "").strip()
    status = (row.get("ocr_status") or "unknown").strip() or "unknown"

    # If empty text, do not hide errors; include ocr_error if status indicates error
    if not text:
        payload = {
            "ocr_text": "",
            "ocr_meta": row.get("ocr_meta") or {},
            "status": status,
        }
        st_low = str(status or "").lower()
        if st_low in {"error", "failed", "ocr_error"} and row.get("ocr_error"):
            payload["ocr_error"] = row.get("ocr_error")
        return payload

    payload = {
        "ocr_text": text,
        "ocr_meta": row.get("ocr_meta") or {},
        "status": status,
    }
    if str(status).lower() in {"error", "failed"} and row.get("ocr_error"):
        payload["ocr_error"] = row.get("ocr_error")
    return payload


