from fastapi.testclient import TestClient

from backend.app import app


def _auth_headers(user_id="owner-1"):
    return {"Authorization": f"Bearer user:{user_id}"}


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
    async def fake_run_grade(*_args, **_kwargs):
        return {"ok": True}

    monkeypatch.setattr("backend.api.uploads.run_grade_pipeline", fake_run_grade)

    client = TestClient(app)
    r = client.post("/api/ocr/start", json={"upload_id": "u3"}, headers=_auth_headers())
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["ok"] is True
    assert data["status"] == "done"
    assert rows["u3"]["ocr_status"] == "done"


def test_ocr_triggers_auto_grade(fake_supabase, monkeypatch):
    rows = fake_supabase._db["uploads"]
    rows["u4"] = {
        "id": "u4",
        "owner_id": "owner-1",
        "storage_path": "submissions/owner-1/u4.png",
        "status": "uploading",
    }
    fake_supabase.storage.objects[("submissions", "owner-1/u4.png")] = b"fake"

    async def fake_extract_text(*_args, **_kwargs):
        return {"text": "hello world", "pages": None, "confidence": 0.9}

    called = {"ok": False}

    async def fake_run_grade(upload_id, user_id, **_kwargs):
        called["ok"] = True
        return {"ok": True, "upload_id": upload_id}

    monkeypatch.setattr("backend.api.ocr.ocr.extract_text", fake_extract_text)
    monkeypatch.setattr("backend.api.uploads.run_grade_pipeline", fake_run_grade)

    client = TestClient(app)
    r = client.post("/api/ocr/start", json={"upload_id": "u4"}, headers=_auth_headers())
    assert r.status_code == 200, r.text
    assert called["ok"] is True
