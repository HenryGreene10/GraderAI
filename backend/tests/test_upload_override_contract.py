from fastapi.testclient import TestClient

from backend.app import app


def _auth_headers(user_id="owner-1"):
    return {"Authorization": f"Bearer user:{user_id}"}


def test_upload_override_persists(fake_supabase):
    rows = fake_supabase._db["uploads"]
    rows["u7"] = {
        "id": "u7",
        "owner_id": "owner-1",
        "storage_path": "submissions/owner-1/u7.pdf",
        "status": "graded",
    }

    client = TestClient(app)
    resp = client.post(
        "/api/uploads/u7/override",
        headers=_auth_headers(),
        json={"overall_status": "correct", "note": "Fixed grading"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "overridden"

    assert fake_supabase._db["overrides"]
    saved = fake_supabase._db["overrides"][0]
    assert saved["upload_id"] == "u7"
    assert saved["overrides_json"]["overall_status"] == "correct"
    assert saved["overrides_json"]["note"] == "Fixed grading"
    assert rows["u7"]["status"] == "overridden"
