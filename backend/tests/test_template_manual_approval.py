from fastapi.testclient import TestClient

from backend.app import app


def _auth_headers(user_id: str = "owner-1") -> dict[str, str]:
    return {"Authorization": f"Bearer user:{user_id}"}


def _blocked_template_regions() -> dict:
    return {
        "version": 1,
        "template_width_px": 1000,
        "template_height_px": 1400,
        "regions": [{"qid": "Q1", "bbox_px": [100, 200, 80, 40], "expected_answer_text": "23 R0"}],
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
                    "expected_answer_text": "23 R0",
                }
            ],
        },
        "manifest_approval_blocked": True,
        "manifest_approval_block_reasons": ["ANCHOR_AMBIGUITY_HIGH"],
    }


def test_manual_template_approval_disabled_by_default(fake_supabase):
    db = fake_supabase._db
    db["assignments"]["a1"] = {
        "id": "a1",
        "owner_id": "owner-1",
        "template_storage_path": "submissions/owner-1/templates/a1.png",
        "template_width_px": 1000,
        "template_height_px": 1400,
        "template_version": 1,
        "template_regions_json": _blocked_template_regions(),
    }

    client = TestClient(app)
    resp = client.post("/api/assignments/a1/template/approve", headers=_auth_headers(), json={})
    assert resp.status_code == 403, resp.text
    assert "manual_template_approval_disabled" in resp.text


def test_manual_template_approval_requires_force_when_blocked(fake_supabase, monkeypatch):
    monkeypatch.setenv("ALLOW_TEMPLATE_MANUAL_APPROVAL", "1")
    db = fake_supabase._db
    db["assignments"]["a1"] = {
        "id": "a1",
        "owner_id": "owner-1",
        "template_storage_path": "submissions/owner-1/templates/a1.png",
        "template_width_px": 1000,
        "template_height_px": 1400,
        "template_version": 1,
        "template_regions_json": _blocked_template_regions(),
    }

    client = TestClient(app)
    resp = client.post("/api/assignments/a1/template/approve", headers=_auth_headers(), json={"force": False})
    assert resp.status_code == 409, resp.text
    assert "template_manifest_blocked" in resp.text


def test_manual_template_approval_force_locks_manifest(fake_supabase, monkeypatch):
    monkeypatch.setenv("ALLOW_TEMPLATE_MANUAL_APPROVAL", "1")
    db = fake_supabase._db
    db["assignments"]["a1"] = {
        "id": "a1",
        "owner_id": "owner-1",
        "template_storage_path": "submissions/owner-1/templates/a1.png",
        "template_width_px": 1000,
        "template_height_px": 1400,
        "template_version": 1,
        "template_regions_json": _blocked_template_regions(),
    }

    client = TestClient(app)
    resp = client.post("/api/assignments/a1/template/approve", headers=_auth_headers(), json={"force": True})
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["ok"] is True
    assert payload["manifest_locked"] is True
    assert payload["manual_approved"] is True
    assert payload["forced"] is True
    assert payload["prior_block_reasons"] == ["ANCHOR_AMBIGUITY_HIGH"]

    assignment = db["assignments"]["a1"]
    regions = assignment.get("template_regions_json") or {}
    assert regions.get("manifest_locked") is True
    assert regions.get("manifest_manual_approved") is True
    assert regions.get("manifest_approval_blocked") is False
    assert regions.get("manifest_approval_block_reasons") == []
