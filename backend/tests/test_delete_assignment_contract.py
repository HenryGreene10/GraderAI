from fastapi.testclient import TestClient

from backend.app import app


def _auth_headers(user_id="owner-1"):
    return {"Authorization": f"Bearer user:{user_id}"}


def test_delete_assignment_cascades(fake_supabase):
    db = fake_supabase._db
    db["assignments"]["a1"] = {"id": "a1", "owner_id": "owner-1"}
    db["uploads"]["u1"] = {
        "id": "u1",
        "owner_id": "owner-1",
        "assignment_id": "a1",
        "storage_path": "submissions/owner-1/a1/u1.pdf",
        "graded_pdf_path": "owner-1/u1.pdf",
        "overlay_path": "owner-1/u1.json",
    }
    db["uploads"]["u2"] = {
        "id": "u2",
        "owner_id": "owner-1",
        "assignment_id": "a1",
        "storage_path": "submissions/owner-1/a1/u2.pdf",
        "graded_pdf_path": "owner-1/u2.pdf",
        "overlay_path": "owner-1/u2.json",
    }

    fake_supabase.storage.objects[("submissions", "owner-1/a1/u1.pdf")] = b"sub1"
    fake_supabase.storage.objects[("submissions", "owner-1/a1/u2.pdf")] = b"sub2"
    fake_supabase.storage.objects[("graded-pdfs", "owner-1/u1.pdf")] = b"pdf1"
    fake_supabase.storage.objects[("graded-pdfs", "owner-1/u2.pdf")] = b"pdf2"
    fake_supabase.storage.objects[("overlays", "owner-1/u1.json")] = b"ov1"
    fake_supabase.storage.objects[("overlays", "owner-1/u2.json")] = b"ov2"

    client = TestClient(app)
    resp = client.delete("/api/assignments/a1", headers=_auth_headers())
    assert resp.status_code == 200, resp.text

    assert "a1" not in db["assignments"]
    assert "u1" not in db["uploads"]
    assert "u2" not in db["uploads"]
    assert ("submissions", "owner-1/a1/u1.pdf") in fake_supabase.storage.removed
    assert ("submissions", "owner-1/a1/u2.pdf") in fake_supabase.storage.removed
    assert ("graded-pdfs", "owner-1/u1.pdf") in fake_supabase.storage.removed
    assert ("graded-pdfs", "owner-1/u2.pdf") in fake_supabase.storage.removed
    assert ("overlays", "owner-1/u1.json") in fake_supabase.storage.removed
    assert ("overlays", "owner-1/u2.json") in fake_supabase.storage.removed
