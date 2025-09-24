# backend/config.py
import os
from dotenv import load_dotenv; load_dotenv()

# Product defaults (in code)
DEFAULT_OCR_PROVIDER = "hf"
DEFAULT_HF_MODEL_ID  = "microsoft/trocr-base-handwritten"  # <- your default

# Secrets/flags (env or .env)
HF_TOKEN      = os.getenv("HF_TOKEN")              # required for HF
OCR_PROVIDER  = os.getenv("OCR_PROVIDER", DEFAULT_OCR_PROVIDER)
HF_MODEL_ID   = os.getenv("HF_MODEL_ID", DEFAULT_HF_MODEL_ID)
REQUIRE_OWNER = os.getenv("REQUIRE_OWNER", "1") == "1"

def summary(safe: bool = True) -> dict:
    out = {
        "ocr_provider": OCR_PROVIDER,
        "hf_model_id": HF_MODEL_ID,
        "require_owner": REQUIRE_OWNER,
    }
    if not safe:
        out["hf_token_present"] = bool(HF_TOKEN)
    return out
