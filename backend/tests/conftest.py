import os
import pytest


@pytest.fixture(autouse=True)
def test_env(monkeypatch):
    # Default all tests to mock the OCR provider and no network
    monkeypatch.setenv("OCR_MOCK", "1")
    monkeypatch.setenv("OCR_PROVIDER", "hf")
    monkeypatch.setenv("HF_TOKEN", "test-token")
    monkeypatch.setenv("SUPABASE_BUCKET", "submissions")
    monkeypatch.setenv("CORS_ALLOW_ORIGINS", "http://localhost:5173")
    # Back-compat for any legacy reads
    monkeypatch.setenv("HANDWRITINGOCR_API_KEY", "test-key")

