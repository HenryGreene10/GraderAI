from fastapi.testclient import TestClient

from backend.app import app


def _auth_headers(user_id="owner-1"):
    return {"X-Owner-Id": user_id, "X-User-Id": user_id}


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
