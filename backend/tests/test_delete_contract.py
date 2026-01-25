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


def test_delete_bucket_relative_and_ok(fake_supabase):
    rows = fake_supabase._db["uploads"]
    rows["u5"] = {
        "id": "u5",
        "owner_id": "owner-1",
        "storage_path": "submissions/owner-1/assign/file3.png",
        "status": "pending",
    }
    client = TestClient(app)

    r = client.delete("/api/uploads/u5", headers=_auth_headers())
    assert r.status_code == 200
    assert r.json().get("ok") is True
    assert ("submissions", "owner-1/assign/file3.png") in fake_supabase.storage.removed
