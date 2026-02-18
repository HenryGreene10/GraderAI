from fastapi.testclient import TestClient

from backend.app import app


def _auth_headers(user_id="owner-1"):
    return {"Authorization": f"Bearer user:{user_id}"}


def test_get_assignment_detail_ok(fake_supabase):
    db = fake_supabase._db
    db["assignments"]["a1"] = {
        "id": "a1",
        "owner_id": "owner-1",
        "title": "Unit 1",
        "due_date": None,
        "created_at": "2026-01-28T00:00:00Z",
        "rubric_json": {"description": "Algebra"},
    }

    client = TestClient(app)
    resp = client.get("/api/assignments/a1", headers=_auth_headers())
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["assignment"]["id"] == "a1"
    assert data["assignment"]["title"] == "Unit 1"
    assert data["assignment"]["template_manifest_locked"] is False
    assert data["assignment"]["template_approval_blocked"] is False
    assert data["assignment"]["template_approval_block_reasons"] == []
    assert data["assignment"]["master_key_status"] == "DRAFT"
    assert data["assignment"]["master_key_ready"] is False


def test_get_assignment_detail_reports_template_approval_state(fake_supabase):
    db = fake_supabase._db
    db["assignments"]["a1"] = {
        "id": "a1",
        "owner_id": "owner-1",
        "title": "Unit 1",
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
    resp = client.get("/api/assignments/a1", headers=_auth_headers())
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["assignment"]["template_manifest_locked"] is False
    assert data["assignment"]["template_approval_blocked"] is True
    assert data["assignment"]["template_approval_block_reasons"] == ["ANCHOR_AMBIGUITY_HIGH"]
    assert data["assignment"]["master_key_status"] == "NEEDS_REUPLOAD"
    assert data["assignment"]["master_key_ready"] is False


def test_get_assignment_detail_reports_master_key_ready(fake_supabase):
    db = fake_supabase._db
    db["assignments"]["a1"] = {
        "id": "a1",
        "owner_id": "owner-1",
        "title": "Unit 1",
        "template_storage_path": "submissions/owner-1/templates/a1.png",
        "template_width_px": 1000,
        "template_height_px": 1400,
        "template_version": 2,
        "template_regions_json": {
            "version": 1,
            "template_width_px": 1000,
            "template_height_px": 1400,
            "regions": [{"qid": "Q1", "bbox_px": [100, 200, 80, 40]}],
            "manifest": {
                "schema_version": "template_manifest.v1",
                "template_version": 2,
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
            "master_key_status": "READY",
        },
    }

    client = TestClient(app)
    resp = client.get("/api/assignments/a1", headers=_auth_headers())
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["assignment"]["master_key_status"] == "READY"
    assert data["assignment"]["master_key_ready"] is True
