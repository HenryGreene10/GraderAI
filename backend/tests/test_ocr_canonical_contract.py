import pytest

from backend.api.ocr import run_ocr_for_upload
from backend.services.scan_pipeline import ScanArtifacts


@pytest.mark.asyncio
async def test_run_ocr_preserves_canonical_size_when_scan_artifacts_differ(monkeypatch):
    updates: list[dict] = []

    row = {
        "id": "u1",
        "owner_id": "owner-1",
        "storage_path": "submissions/owner-1/u1.png",
        "mime_type": "image/png",
        "needs_review": False,
        "normalized_image_path": None,
        "normalized_width_px": 1750,
        "normalized_height_px": 2479,
        "page_sizes_json": [{"width_px": 1750, "height_px": 2479}],
    }

    artifacts = ScanArtifacts(
        normalized_image_path="submissions/owner-1/normalized/u1.png",
        normalized_pdf_path="submissions/owner-1/normalized/u1.pdf",
        width_px=900,
        height_px=1300,
        scan_ok=True,
        error=None,
        normalized_image_bytes=b"fake-normalized-image",
        normalized_pdf_bytes=b"%PDF-1.4 mock",
    )

    monkeypatch.setattr("backend.api.ocr.get_upload", lambda *_args, **_kwargs: row)
    monkeypatch.setattr("backend.api.ocr.update_upload", lambda _id, payload: updates.append(dict(payload)))
    monkeypatch.setattr("backend.api.ocr.download_submission_bytes", lambda _path: b"fake-image")
    monkeypatch.setattr("backend.api.ocr.prepare_ocr_image", lambda *_args, **_kwargs: (artifacts.normalized_image_bytes, artifacts))

    async def fake_extract_text(*_args, **_kwargs):
        return {
            "text": "Q1 4",
            "boxes": {
                "analyzeResult": {
                    "readResults": [
                        {
                            "width": 900,
                            "height": 1300,
                            "lines": [],
                        }
                    ]
                }
            },
            "confidence": 0.98,
        }

    monkeypatch.setattr("backend.api.ocr.ocr.extract_text", fake_extract_text)
    monkeypatch.setattr("backend.api.ocr.normalize_ocr_result", lambda raw: raw)

    async def fake_run_grade_pipeline(*_args, **_kwargs):
        return {"ok": True}

    monkeypatch.setattr("backend.api.uploads.run_grade_pipeline", fake_run_grade_pipeline)

    result = await run_ocr_for_upload("u1", "owner-1")

    assert result["ok"] is True
    assert len(updates) >= 2
    final_payload = updates[-1]
    assert final_payload["normalized_width_px"] == 1750
    assert final_payload["normalized_height_px"] == 2479
