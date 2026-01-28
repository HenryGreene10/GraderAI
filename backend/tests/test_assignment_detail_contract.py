from fastapi.testclient import TestClient

from backend.app import app


def _auth_headers(user_id="owner-1"):
    return {"Authorization": f"Bearer user:{user_id}"}


def test_get_assignment_detail_ok(fake_supabase):
    db = fake_supabase._db
    db["assignments"]["a1"] = {
        "id": "a1",
        "owner_id": "owner-1",
        "title": "Unit 1",
        "due_date": None,
        "created_at": "2026-01-28T00:00:00Z",
        "rubric_json": {"description": "Algebra"},
    }

    client = TestClient(app)
    resp = client.get("/api/assignments/a1", headers=_auth_headers())
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["assignment"]["id"] == "a1"
    assert data["assignment"]["title"] == "Unit 1"
