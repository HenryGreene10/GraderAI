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


def test_grade_requires_auth():
    client = TestClient(app)
    r = client.post("/api/grade", json={"upload_id": "u6"})
    assert r.status_code == 401


def test_grade_returns_scores(fake_supabase, monkeypatch):
    rows = fake_supabase._db["uploads"]
    rows["u6"] = {
        "id": "u6",
        "owner_id": "owner-1",
        "storage_path": "submissions/owner-1/a.png",
        "status": "pending",
    }
    fake_supabase.storage.objects[("submissions", "owner-1/a.png")] = b"fake"

    async def fake_extract_text(*_args, **_kwargs):
        return {"text": "2+2=4\nQ: add two numbers", "pages": None, "confidence": 0.9}

    monkeypatch.setattr("backend.api.grade.ocr_service.extract_text", fake_extract_text)

    client = TestClient(app)
    r = client.post("/api/grade", json={"upload_id": "u6"}, headers=_auth_headers())
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["ok"] is True
    assert isinstance(data["grade"]["total_score"], (int, float))
    assert isinstance(data["grade"]["items"], list)
