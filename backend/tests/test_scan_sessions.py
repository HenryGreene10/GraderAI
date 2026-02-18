from io import BytesIO

from fastapi.testclient import TestClient
from PIL import Image
from pypdf import PdfWriter

from backend.app import app


def _auth_headers(user_id="owner-1"):
    return {"Authorization": f"Bearer user:{user_id}"}


def _pdf_bytes(pages=None):
    pages = pages or [(420, 595)]
    writer = PdfWriter()
    for width, height in pages:
        writer.add_blank_page(width=width, height=height)
    buf = BytesIO()
    writer.write(buf)
    return buf.getvalue()


def _png_bytes(width=600, height=900):
    img = Image.new("RGB", (width, height), color=(255, 255, 255))
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_scan_session_student_upload(fake_supabase, monkeypatch):
    db = fake_supabase._db
    db["assignments"]["a1"] = {"id": "a1", "owner_id": "owner-1", "title": "A1"}
    async def _noop(*_args, **_kwargs):
        return None
    monkeypatch.setattr("backend.api.scan._run_ocr_and_grade", _noop)

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

    page_sizes = [(420, 595), (420, 595)]
    pdf_bytes = _pdf_bytes(page_sizes)
    resp = client.post(
        f"/api/scan/{token}/upload",
        files={"file": ("scan.pdf", pdf_bytes, "application/pdf")},
        data={
            "page_count": str(len(page_sizes)),
            "page_sizes": "[{\"width_px\": 420, \"height_px\": 595}, {\"width_px\": 420, \"height_px\": 595}]",
        },
    )
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    upload_id = payload.get("resulting_upload_id")
    assert upload_id
    upload_row = db["uploads"][upload_id]
    assert upload_row["page_count"] == 2
    assert upload_row["page_sizes_json"] == [
        {"width_px": 420, "height_px": 595},
        {"width_px": 420, "height_px": 595},
    ]

    session_rows = list(db["scan_sessions"].values())
    assert session_rows
    session = session_rows[0]
    assert session["status"] == "active"
    assert session["resulting_upload_id"] == upload_id

    storage_key = f"owner-1/{upload_id}.pdf"
    assert ("submissions", storage_key) in fake_supabase.storage.objects


def test_scan_session_student_upload_saves_normalized_image_artifact(fake_supabase, monkeypatch):
    db = fake_supabase._db
    db["assignments"]["a1"] = {"id": "a1", "owner_id": "owner-1", "title": "A1"}
    async def _noop(*_args, **_kwargs):
        return None
    monkeypatch.setattr("backend.api.scan._run_ocr_and_grade", _noop)

    client = TestClient(app)
    create = client.post(
        "/api/assignments/a1/scan-sessions",
        headers=_auth_headers(),
        json={"mode": "student"},
    )
    assert create.status_code == 200, create.text
    token = create.json().get("token")
    assert token

    pdf_bytes = _pdf_bytes([(420, 595)])
    png_bytes = _png_bytes(640, 960)
    resp = client.post(
        f"/api/scan/{token}/upload",
        files=[
            ("file", ("scan.pdf", pdf_bytes, "application/pdf")),
            ("normalized_image", ("normalized-page-1.png", png_bytes, "image/png")),
        ],
        data={"page_count": "1"},
    )
    assert resp.status_code == 200, resp.text
    upload_id = resp.json().get("resulting_upload_id")
    assert upload_id
    row = db["uploads"][upload_id]
    assert row["normalized_image_path"] == f"submissions/owner-1/normalized/{upload_id}.png"
    assert row["normalized_pdf_path"] == f"submissions/owner-1/{upload_id}.pdf"
    assert row["normalized_width_px"] == 640
    assert row["normalized_height_px"] == 960
    assert row["scan_status"] == "normalized"
