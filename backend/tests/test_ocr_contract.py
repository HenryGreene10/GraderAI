import base64
import hashlib
import hmac
import json
import os
import time

from fastapi.testclient import TestClient

from backend.app import app


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("utf-8")


def _make_token(user_id="owner-1"):
    secret = os.environ.get("SUPABASE_JWT_SECRET", "")
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {"sub": user_id, "exp": int(time.time()) + 3600}
    header_b64 = _b64url(json.dumps(header).encode("utf-8"))
    payload_b64 = _b64url(json.dumps(payload).encode("utf-8"))
    signing_input = f"{header_b64}.{payload_b64}".encode("utf-8")
    sig = hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
    return f"{header_b64}.{payload_b64}.{_b64url(sig)}"


def _auth_headers(user_id="owner-1"):
    return {"Authorization": f"Bearer {_make_token(user_id)}"}


def test_ocr_requires_auth():
    client = TestClient(app)
    r = client.post("/api/ocr/start", json={"upload_id": "u3"})
    assert r.status_code == 401


def test_ocr_start_happy_path(fake_supabase, monkeypatch):
    rows = fake_supabase._db["uploads"]
    rows["u3"] = {
        "id": "u3",
        "owner_id": "owner-1",
        "storage_path": "submissions/owner-1/z.png",
        "status": "pending",
    }
    fake_supabase.storage.objects[("submissions", "owner-1/z.png")] = b"fake"

    async def fake_extract_text(*_args, **_kwargs):
        return {"text": "hello world", "pages": None, "confidence": 0.9}

    monkeypatch.setattr("backend.api.ocr.ocr.extract_text", fake_extract_text)

    client = TestClient(app)
    r = client.post("/api/ocr/start", json={"upload_id": "u3"}, headers=_auth_headers())
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["ok"] is True
    assert data["status"] == "done"
    assert rows["u3"]["ocr_status"] == "done"


def test_ocr_error_returns_500(fake_supabase, monkeypatch):
    rows = fake_supabase._db["uploads"]
    rows["u4"] = {
        "id": "u4",
        "owner_id": "owner-1",
        "storage_path": "submissions/owner-1/t.png",
        "status": "pending",
    }
    fake_supabase.storage.objects[("submissions", "owner-1/t.png")] = b"fake"

    async def raise_error(*_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr("backend.api.ocr.ocr.extract_text", raise_error)

    client = TestClient(app)
    r = client.post("/api/ocr/start", json={"upload_id": "u4"}, headers=_auth_headers())
    assert r.status_code == 500
    assert rows["u4"]["ocr_status"] == "failed"
