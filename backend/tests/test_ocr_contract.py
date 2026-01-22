from fastapi.testclient import TestClient

from backend.app import app


def _auth_headers(user_id="owner-1"):
    return {"X-Owner-Id": user_id, "X-User-Id": user_id}


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
