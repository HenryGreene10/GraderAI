import pytest
from fastapi import HTTPException

from backend.api.uploads import run_grade_pipeline


@pytest.mark.asyncio
async def test_assignment_lookup_failure_is_not_silent(monkeypatch):
    seen: dict[str, str] = {}
    updates: list[dict] = []

    def fake_get_upload(_upload_id, _caller_id, columns="*"):
        return {
            "id": "u1",
            "owner_id": "owner-1",
            "assignment_id": "a1",
            "storage_path": "submissions/owner-1/u1.pdf",
            "ocr_status": "done",
            "ocr_text": "Q1 2 R0",
            "ocr_boxes": {"analyzeResult": {"readResults": []}},
            "mime_type": "application/pdf",
            "status": "ocr_done",
            "graded_pdf_path": None,
            "normalized_pdf_path": None,
            "normalized_width_px": 1000,
            "normalized_height_px": 1000,
            "needs_review": False,
            "normalized_image_path": None,
        }

    def fake_get_assignment(_assignment_id, _caller_id, columns="*"):
        seen["columns"] = str(columns)
        raise HTTPException(status_code=403, detail="Forbidden")

    def fake_update_upload(_upload_id, payload):
        updates.append(dict(payload))

    monkeypatch.setattr("backend.api.uploads.get_upload", fake_get_upload)
    monkeypatch.setattr("backend.api.uploads.get_assignment", fake_get_assignment)
    monkeypatch.setattr("backend.api.uploads.update_upload", fake_update_upload)

    with pytest.raises(HTTPException) as exc:
        await run_grade_pipeline("u1", "owner-1")

    assert exc.value.status_code == 409
    assert "assignment_lookup_failed" in str(exc.value.detail)
    assert "owner_id" in seen.get("columns", "")
    assert any(str(item.get("ocr_error") or "").startswith("assignment_lookup_failed:") for item in updates)
