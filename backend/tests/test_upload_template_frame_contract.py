from io import BytesIO

import pytest
from fastapi import HTTPException
from PIL import Image

from backend.api.uploads import run_grade_pipeline


def _png_bytes(width: int, height: int) -> bytes:
    buf = BytesIO()
    Image.new("RGB", (width, height), color=(255, 255, 255)).save(buf, format="PNG")
    return buf.getvalue()


def _approved_regions_payload() -> dict:
    return {
        "version": 1,
        "page_index": 0,
        "template_width_px": 1200,
        "template_height_px": 1600,
        "regions": [
            {
                "qid": "Q1",
                "page_index": 0,
                "bbox_px": [200, 250, 120, 60],
                "expected_answer_text": "4",
            }
        ],
        "manifest": {
            "schema_version": "template_manifest.v1",
            "template_version": 1,
            "template_width_px": 1200,
            "template_height_px": 1600,
            "question_count": 1,
            "questions": [
                {
                    "question_id": "Q1",
                    "page_index": 0,
                    "answer_box_px": [200, 250, 120, 60],
                    "expected_answer_text": "4",
                }
            ],
        },
        "manifest_locked": True,
        "manifest_approved_at": "2026-02-18T00:00:00Z",
    }


def _base_upload_row() -> dict:
    return {
        "id": "u1",
        "owner_id": "owner-1",
        "assignment_id": "a1",
        "storage_path": "submissions/owner-1/u1.pdf",
        "ocr_status": "done",
        "ocr_text": "Q1 4",
        "ocr_boxes": {"analyzeResult": {"readResults": []}},
        "mime_type": "application/pdf",
        "status": "ocr_done",
        "graded_pdf_path": None,
        "normalized_pdf_path": "submissions/owner-1/u1.pdf",
        "normalized_width_px": 1750,
        "normalized_height_px": 2479,
        "page_sizes_json": [{"width_px": 1750, "height_px": 2479}],
        "needs_review": False,
        "normalized_image_path": None,
    }


def _base_assignment_row() -> dict:
    return {
        "id": "a1",
        "owner_id": "owner-1",
        "template_storage_path": "submissions/owner-1/templates/a1.png",
        "template_regions_json": _approved_regions_payload(),
        "template_version": 1,
        "template_width_px": 1200,
        "template_height_px": 1600,
    }


@pytest.mark.asyncio
async def test_template_grading_blocks_on_student_frame_size_mismatch(monkeypatch):
    updates: list[dict] = []
    row = _base_upload_row()
    assignment = _base_assignment_row()

    monkeypatch.setattr("backend.api.uploads.get_upload", lambda *_args, **_kwargs: row)
    monkeypatch.setattr("backend.api.uploads.get_assignment", lambda *_args, **_kwargs: assignment)
    monkeypatch.setattr("backend.api.uploads.update_upload", lambda _id, payload: updates.append(dict(payload)))

    def fake_download(path: str) -> bytes:
        if path.endswith("a1.png"):
            return _png_bytes(1200, 1600)
        return b"%PDF-1.4 mock"

    monkeypatch.setattr("backend.api.uploads.download_submission_bytes", fake_download)
    monkeypatch.setattr(
        "backend.api.uploads.extract_pdf_page_raster_png",
        lambda *_args, **_kwargs: (_png_bytes(1000, 1400), "largest_embedded_raster"),
    )

    with pytest.raises(HTTPException) as exc:
        await run_grade_pipeline("u1", "owner-1")

    assert exc.value.status_code == 422
    detail = str(exc.value.detail)
    assert "template_frame_unavailable" in detail
    assert "student_frame_size_mismatch" in detail
    assert "upload a scanned pdf" in detail.lower()
    assert any("student_frame_size_mismatch" in str(item.get("ocr_error") or "") for item in updates)


@pytest.mark.asyncio
async def test_template_grading_blocks_when_student_pdf_frame_unavailable(monkeypatch):
    updates: list[dict] = []
    row = _base_upload_row()
    assignment = _base_assignment_row()

    monkeypatch.setattr("backend.api.uploads.get_upload", lambda *_args, **_kwargs: row)
    monkeypatch.setattr("backend.api.uploads.get_assignment", lambda *_args, **_kwargs: assignment)
    monkeypatch.setattr("backend.api.uploads.update_upload", lambda _id, payload: updates.append(dict(payload)))
    monkeypatch.setattr(
        "backend.api.uploads.download_submission_bytes",
        lambda path: _png_bytes(1200, 1600) if path.endswith("a1.png") else b"%PDF-1.4 mock",
    )

    def fake_extract(*_args, **_kwargs):
        raise RuntimeError("raster_failed")

    monkeypatch.setattr("backend.api.uploads.extract_pdf_page_raster_png", fake_extract)

    with pytest.raises(HTTPException) as exc:
        await run_grade_pipeline("u1", "owner-1")

    assert exc.value.status_code == 422
    detail = str(exc.value.detail)
    assert "template_frame_unavailable" in detail
    assert "student_pdf_raster_failed" in detail
    assert "upload a scanned pdf" in detail.lower()
    assert any("student_pdf_raster_failed" in str(item.get("ocr_error") or "") for item in updates)
