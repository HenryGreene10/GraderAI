from io import BytesIO

from fastapi.testclient import TestClient
from pypdf import PdfWriter

from backend.app import app


def _auth_headers(user_id="owner-1"):
    return {"Authorization": f"Bearer user:{user_id}"}


def _blank_pdf_bytes(width=420, height=595):
    writer = PdfWriter()
    writer.add_blank_page(width=width, height=height)
    buf = BytesIO()
    writer.write(buf)
    return buf.getvalue()


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


def test_student_upload_requires_approved_master_key(fake_supabase):
    db = fake_supabase._db
    db["assignments"]["a1"] = {
        "id": "a1",
        "owner_id": "owner-1",
        "template_storage_path": "submissions/owner-1/templates/a1.png",
        "template_width_px": 1000,
        "template_height_px": 1400,
        "template_version": 1,
        "template_regions_json": {
            "version": 1,
            "template_width_px": 1000,
            "template_height_px": 1400,
            "regions": [{"qid": "Q1", "bbox_px": [100, 200, 80, 40]}],
            "manifest": {
                "schema_version": "template_manifest.v1",
                "template_version": 1,
                "template_width_px": 1000,
                "template_height_px": 1400,
                "question_count": 1,
                "questions": [
                    {
                        "question_id": "Q1",
                        "page_index": 0,
                        "answer_box_px": [100, 200, 80, 40],
                    }
                ],
            },
            "manifest_approval_blocked": True,
            "manifest_approval_block_reasons": ["ANCHOR_AMBIGUITY_HIGH"],
        },
    }

    client = TestClient(app)
    resp = client.post(
        "/api/assignments/a1/uploads",
        headers=_auth_headers(),
        files={"files": ("student.pdf", b"%PDF-1.4 mock", "application/pdf")},
    )

    assert resp.status_code == 409, resp.text
    data = resp.json()
    detail = str(data.get("detail") or "")
    assert "not ready" in detail.lower()


def test_student_upload_pdf_only_and_sets_canonical_page_metadata(fake_supabase, monkeypatch):
    db = fake_supabase._db
    db["assignments"]["a1"] = {
        "id": "a1",
        "owner_id": "owner-1",
        "template_storage_path": "submissions/owner-1/templates/a1.png",
        "template_width_px": 1000,
        "template_height_px": 1400,
        "template_version": 1,
        "template_regions_json": {
            "version": 1,
            "template_width_px": 1000,
            "template_height_px": 1400,
            "regions": [{"qid": "Q1", "bbox_px": [100, 200, 80, 40]}],
            "manifest": {
                "schema_version": "template_manifest.v1",
                "template_version": 1,
                "template_width_px": 1000,
                "template_height_px": 1400,
                "question_count": 1,
                "questions": [
                    {
                        "question_id": "Q1",
                        "page_index": 0,
                        "answer_box_px": [100, 200, 80, 40],
                    }
                ],
            },
            "manifest_locked": True,
            "manifest_approved_at": "2026-02-18T00:00:00Z",
        },
    }

    async def _noop(*_args, **_kwargs):
        return None

    monkeypatch.setattr("backend.api.assignments._run_ocr_in_background", _noop)

    client = TestClient(app)
    pdf_bytes = _blank_pdf_bytes(420, 595)
    resp = client.post(
        "/api/assignments/a1/uploads",
        headers=_auth_headers(),
        files={"files": ("student.pdf", pdf_bytes, "application/pdf")},
    )
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    uploads = payload.get("uploads") or []
    assert len(uploads) == 1
    upload_id = uploads[0].get("id")
    assert upload_id
    row = db["uploads"][upload_id]
    assert row["page_count"] == 1
    assert row["page_sizes_json"] == [{"width_px": 1750, "height_px": 2479}]
    assert row["normalized_width_px"] == 1750
    assert row["normalized_height_px"] == 2479
    assert row["normalized_pdf_path"] == row["storage_path"]


def test_student_upload_rejects_images_in_pdf_first_flow(fake_supabase):
    db = fake_supabase._db
    db["assignments"]["a1"] = {
        "id": "a1",
        "owner_id": "owner-1",
        "template_storage_path": "submissions/owner-1/templates/a1.png",
        "template_width_px": 1000,
        "template_height_px": 1400,
        "template_version": 1,
        "template_regions_json": {
            "version": 1,
            "template_width_px": 1000,
            "template_height_px": 1400,
            "regions": [{"qid": "Q1", "bbox_px": [100, 200, 80, 40]}],
            "manifest": {
                "schema_version": "template_manifest.v1",
                "template_version": 1,
                "template_width_px": 1000,
                "template_height_px": 1400,
                "question_count": 1,
                "questions": [
                    {
                        "question_id": "Q1",
                        "page_index": 0,
                        "answer_box_px": [100, 200, 80, 40],
                    }
                ],
            },
            "manifest_locked": True,
            "manifest_approved_at": "2026-02-18T00:00:00Z",
        },
    }

    client = TestClient(app)
    resp = client.post(
        "/api/assignments/a1/uploads",
        headers=_auth_headers(),
        files={"files": ("student.png", b"fake-image", "image/png")},
    )
    assert resp.status_code == 400, resp.text
    assert "pdf_required" in str(resp.json().get("detail") or "").lower()
