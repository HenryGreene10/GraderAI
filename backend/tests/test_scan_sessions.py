from io import BytesIO

from fastapi.testclient import TestClient
from PIL import Image

from backend.app import app


def _auth_headers(user_id="owner-1"):
    return {"Authorization": f"Bearer user:{user_id}"}


def _png_bytes(width=12, height=10):
    img = Image.new("RGB", (width, height), color=(255, 255, 255))
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_scan_session_student_upload(fake_supabase, monkeypatch):
    db = fake_supabase._db
    db["assignments"]["a1"] = {"id": "a1", "owner_id": "owner-1", "title": "A1"}

    async def _fake_run_grade_pipeline(upload_id, user_id, **_kwargs):
        return {"ok": True, "upload_id": upload_id}

    monkeypatch.setattr("backend.api.scan.run_grade_pipeline", _fake_run_grade_pipeline)

    client = TestClient(app)
    resp = client.post(
        "/api/assignments/a1/scan-sessions",
        headers=_auth_headers(),
        json={"mode": "student"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    token = data.get("token")
    assert token

    image_bytes = _png_bytes()
    resp = client.post(
        f"/api/scan/{token}/upload",
        files={"file": ("scan.png", image_bytes, "image/png")},
    )
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    upload_id = payload.get("resulting_upload_id")
    assert upload_id
    upload_row = db["uploads"][upload_id]
    assert upload_row["normalized_width_px"] == 12
    assert upload_row["normalized_height_px"] == 10

    session_rows = list(db["scan_sessions"].values())
    assert session_rows
    session = session_rows[0]
    assert session["status"] == "complete"
    assert session["resulting_upload_id"] == upload_id

    storage_key = f"owner-1/normalized/{upload_id}.png"
    assert ("submissions", storage_key) in fake_supabase.storage.objects
