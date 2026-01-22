def _auth_headers(user_id="owner-1"):
    return {"X-Owner-Id": user_id, "X-User-Id": user_id}


from fastapi.testclient import TestClient

from backend.app import app


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
