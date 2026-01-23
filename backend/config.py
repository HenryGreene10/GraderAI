# backend/config.py
import os
from dotenv import load_dotenv

load_dotenv()

# Product defaults (in code)
DEFAULT_OCR_PROVIDER = "hf"
DEFAULT_HF_MODEL_ID  = "microsoft/trocr-base-handwritten"  # <- your default

# Secrets/flags (env or .env)
HF_TOKEN = os.getenv("HF_TOKEN")  # required for HF
OCR_PROVIDER = os.getenv("OCR_PROVIDER", DEFAULT_OCR_PROVIDER)
OCR_MOCK = os.getenv("OCR_MOCK", "0") == "1"
HF_MODEL_ID = os.getenv("HF_MODEL_ID", DEFAULT_HF_MODEL_ID)
REQUIRE_OWNER = os.getenv("REQUIRE_OWNER", "1") == "1"

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_KEY")
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY")
SUPABASE_JWT_SECRET = os.getenv("SUPABASE_JWT_SECRET")

SUBMISSIONS_BUCKET = os.getenv("SUBMISSIONS_BUCKET", "submissions")
GRADED_BUCKET = os.getenv("GRADED_BUCKET", "graded-pdfs")
OVERLAYS_BUCKET = os.getenv("OVERLAYS_BUCKET", "overlays")

CORS_ALLOW_ORIGINS = [
    o.strip()
    for o in os.getenv("CORS_ALLOW_ORIGINS", "http://localhost:5173").split(",")
    if o.strip()
]

OCR_REVIEW_THRESHOLD = float(os.getenv("OCR_REVIEW_THRESHOLD", "0.85"))

def summary(safe: bool = True) -> dict:
    out = {
        "ocr_provider": OCR_PROVIDER,
        "hf_model_id": HF_MODEL_ID,
        "require_owner": REQUIRE_OWNER,
        "ocr_mock": OCR_MOCK,
        "submissions_bucket": SUBMISSIONS_BUCKET,
        "graded_bucket": GRADED_BUCKET,
        "overlays_bucket": OVERLAYS_BUCKET,
    }
    if not safe:
        out["hf_token_present"] = bool(HF_TOKEN)
    return out
