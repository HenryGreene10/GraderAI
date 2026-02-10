from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from backend.app import app
from backend.services.master_key_pipeline import MasterKeyApprovalResult


def _auth_headers(user_id: str = "owner-1") -> dict[str, str]:
    return {"Authorization": f"Bearer user:{user_id}"}


def _mock_result(assignment_id: str) -> MasterKeyApprovalResult:
    return MasterKeyApprovalResult(
        assignment_id=assignment_id,
        template_storage_path=f"submissions/owner-1/templates/{assignment_id}.png",
        template_version=3,
        template_upload_id="tpl-upload-123",
        template_original_name="master-key.png",
        template_uploaded_at="2026-02-10T00:00:00+00:00",
        boxes_detected=2,
        qids=["Q1", "Q2"],
        warnings=["w1"],
    )


def test_assignment_template_upload_uses_master_key_pipeline(fake_supabase, monkeypatch):
    calls: list[dict] = []

    async def fake_pipeline(**kwargs):
        calls.append(kwargs)
        return _mock_result(kwargs["assignment_id"])

    monkeypatch.setattr("backend.api.assignments.run_master_key_approval_pipeline", fake_pipeline)

    client = TestClient(app)
    resp = client.post(
        "/api/assignments/a1/template",
        headers=_auth_headers(),
        files={"file": ("master-key.png", b"fake-image", "image/png")},
    )

    assert resp.status_code == 200, resp.text
    assert len(calls) == 1
    assert calls[0]["assignment_id"] == "a1"
    assert calls[0]["user_id"] == "owner-1"
    assert calls[0]["payload"] == b"fake-image"
    assert calls[0]["template_original_name"] == "master-key.png"
    assert callable(calls[0]["debug_hook"])

    data = resp.json()
    assert data["template_version"] == 3
    assert data["template_upload_id"] == "tpl-upload-123"
    assert data["qids"] == ["Q1", "Q2"]


def test_scan_master_key_upload_uses_master_key_pipeline(fake_supabase, monkeypatch):
    db = fake_supabase._db
    db["assignments"]["a1"] = {"id": "a1", "owner_id": "owner-1"}
    expires = (datetime.now(timezone.utc) + timedelta(minutes=5)).replace(microsecond=0).isoformat()
    db["scan_sessions"]["s1"] = {
        "id": "s1",
        "token": "scan-token",
        "owner_id": "owner-1",
        "assignment_id": "a1",
        "mode": "master_key",
        "status": "pending",
        "expires_at": expires,
        "resulting_upload_id": None,
    }

    calls: list[dict] = []

    async def fake_pipeline(**kwargs):
        calls.append(kwargs)
        return _mock_result(kwargs["assignment_id"])

    monkeypatch.setattr("backend.api.scan.run_master_key_approval_pipeline", fake_pipeline)

    client = TestClient(app)
    resp = client.post(
        "/api/scan/scan-token/upload",
        files={"file": ("master-key.png", b"scan-image", "image/png")},
    )

    assert resp.status_code == 200, resp.text
    assert len(calls) == 1
    assert calls[0]["assignment_id"] == "a1"
    assert calls[0]["user_id"] == "owner-1"
    assert calls[0]["payload"] == b"scan-image"
    assert calls[0]["template_original_name"] == "master-key.png"
    assert "debug_hook" not in calls[0]

    session = db["scan_sessions"]["s1"]
    assert session["status"] == "complete"
    assert session["resulting_upload_id"] is None

    data = resp.json()
    assert data["mode"] == "master_key"
    assert data["template_version"] == 3
    assert data["template_upload_id"] == "tpl-upload-123"
