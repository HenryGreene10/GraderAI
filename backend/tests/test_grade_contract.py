from fastapi.testclient import TestClient

from backend.app import app


def _auth_headers(user_id="owner-1"):
    return {"Authorization": f"Bearer user:{user_id}"}


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
        "status": "pdf_ready",
        "needs_review": False,
        "graded_pdf_path": "owner-1/u6.pdf",
        "overlay_path": "owner-1/u6.json",
        "grade_json": {"total_score": 1.0, "items": []},
    }

    async def fake_unified(*_args, **_kwargs):
        return {
            "ok": True,
            "upload_id": "u6",
            "needs_review": False,
            "graded_pdf_path": "owner-1/u6.pdf",
            "pipeline_source": "test.unified",
        }

    monkeypatch.setattr("backend.api.grade.run_unified_submission_pipeline", fake_unified)

    client = TestClient(app)
    r = client.post("/api/grade", json={"upload_id": "u6"}, headers=_auth_headers())
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["ok"] is True
    assert isinstance(data["grade"]["total_score"], (int, float))
    assert isinstance(data["grade"]["items"], list)
