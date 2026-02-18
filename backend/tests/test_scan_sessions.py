from fastapi.testclient import TestClient

from backend.app import app


def _auth_headers(user_id="owner-1"):
    return {"Authorization": f"Bearer user:{user_id}"}


def test_scan_session_creation_is_deprecated(fake_supabase):
    db = fake_supabase._db
    db["assignments"]["a1"] = {"id": "a1", "owner_id": "owner-1", "title": "A1"}

    client = TestClient(app)
    resp = client.post(
        "/api/assignments/a1/scan-sessions",
        headers=_auth_headers(),
        json={"mode": "student"},
    )
    assert resp.status_code == 410, resp.text
    detail = str(resp.json().get("detail") or "")
    assert "scan_workflow_deprecated" in detail
