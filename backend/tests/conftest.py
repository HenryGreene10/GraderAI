# backend/tests/conftest.py
import os

# --- Set env before anything imports your app code ---
os.environ["OCR_MOCK"] = "1"
os.environ["OCR_PROVIDER"] = "mock"
os.environ.pop("HF_TOKEN", None)   # ensure HF path isn't used

# optional but harmless defaults you had:
os.environ.setdefault("SUPABASE_BUCKET", "submissions")
os.environ.setdefault("CORS_ALLOW_ORIGINS", "http://localhost:5173")
os.environ.setdefault("HANDWRITINGOCR_API_KEY", "test-key")

import pytest

@pytest.fixture(autouse=True)
def test_env(monkeypatch):
    # keep these so any later code still sees the mock defaults
    monkeypatch.setenv("OCR_MOCK", "1")
    monkeypatch.setenv("OCR_PROVIDER", "mock")
    monkeypatch.delenv("HF_TOKEN", raising=False)
