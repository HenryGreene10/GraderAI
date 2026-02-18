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
    assert "pending approval" in detail.lower()
