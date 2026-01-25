from fastapi.testclient import TestClient

from backend.app import app


def _auth_headers(user_id="owner-1"):
    return {"Authorization": f"Bearer user:{user_id}"}


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
