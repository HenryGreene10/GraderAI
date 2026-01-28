from fastapi.testclient import TestClient

from backend.app import app


def _auth_headers(user_id="owner-1"):
    return {"Authorization": f"Bearer user:{user_id}"}


def test_student_upload_requires_master_key(fake_supabase):
    db = fake_supabase._db
    db["assignments"]["a1"] = {"id": "a1", "owner_id": "owner-1"}

    client = TestClient(app)
    resp = client.post(
        "/api/assignments/a1/uploads",
        headers=_auth_headers(),
        files={"files": ("student.pdf", b"%PDF-1.4 mock", "application/pdf")},
    )

    assert resp.status_code == 409, resp.text
    data = resp.json()
    assert "master key" in data.get("detail", "").lower()
