import pytest

from backend.api.assignments import get_answer_key
from backend.api.uploads import get_student_answers


@pytest.mark.asyncio
async def test_assignment_answer_key_prefers_template_regions(monkeypatch):
    async def _should_not_run(*_args, **_kwargs):
        raise AssertionError("OCR/LLM fallback should not run when template regions exist")

    def fake_get_assignment(_assignment_id, _user_id, columns="*"):
        return {
            "id": "a1",
            "owner_id": "owner-1",
            "template_storage_path": "submissions/owner-1/templates/a1.png",
            "template_regions_json": {
                "regions": [
                    {"qid": "Q1", "expected_answer_text": "23 R0"},
                    {"qid": "Q2", "expected_answer_text": "171 R7"},
                ]
            },
            "template_uploaded_at": "2026-02-12T00:00:00+00:00",
        }

    monkeypatch.setattr("backend.api.assignments.get_assignment", fake_get_assignment)
    monkeypatch.setattr("backend.api.assignments.download_submission_bytes", _should_not_run)
    monkeypatch.setattr("backend.api.assignments.extract_answers_from_ocr", _should_not_run)

    data = await get_answer_key("a1", user_id="owner-1", include_metadata=True)
    assert data["prompt_version"] == "template-regions-v1"
    assert data["answers"] == {"Q1": "23 R0", "Q2": "171 R7"}
    assert data.get("metadata", {}).get("source") == "template_regions"


@pytest.mark.asyncio
async def test_upload_student_answers_prefers_template_regions(monkeypatch):
    async def _should_not_run(*_args, **_kwargs):
        raise AssertionError("LLM fallback should not run when template regions exist")

    def _word(text: str, x: float, y: float, w: float, h: float) -> dict:
        return {
            "text": text,
            "boundingBox": [x, y, x + w, y, x + w, y + h, x, y + h],
        }

    def _line(words: list[dict]) -> dict:
        return {"text": " ".join(str(w.get("text") or "") for w in words), "words": words}

    def fake_get_upload(_upload_id, _user_id, columns="*"):
        return {
            "id": "u1",
            "owner_id": "owner-1",
            "assignment_id": "a1",
            "ocr_status": "done",
            "ocr_error": None,
            "ocr_text": "Q1 11 R3 Q2 22 R1",
            "ocr_confidence": None,
            "normalized_width_px": 1000,
            "normalized_height_px": 1400,
            "ocr_boxes": {
                "analyzeResult": {
                    "readResults": [
                        {
                            "page": 1,
                            "unit": "pixel",
                            "width": 1000,
                            "height": 1400,
                            "lines": [
                                _line([_word("11", 100, 90, 30, 20), _word("R3", 135, 90, 30, 20)]),
                                _line([_word("22", 280, 90, 30, 20), _word("R1", 315, 90, 30, 20)]),
                            ],
                        }
                    ]
                }
            },
        }

    def fake_get_assignment(_assignment_id, _user_id, columns="*"):
        return {
            "id": "a1",
            "owner_id": "owner-1",
            "template_version": 1,
            "template_regions_json": {
                "regions": [
                    {"qid": "Q1", "page_index": 0, "bbox_px": [80, 80, 120, 60]},
                    {"qid": "Q2", "page_index": 0, "bbox_px": [260, 80, 140, 60]},
                ]
            },
        }

    monkeypatch.setattr("backend.api.uploads.get_upload", fake_get_upload)
    monkeypatch.setattr("backend.api.uploads.get_assignment", fake_get_assignment)
    monkeypatch.setattr("backend.api.uploads.extract_answers_from_ocr", _should_not_run)

    data = await get_student_answers("u1", user_id="owner-1", include_metadata=True)
    assert data["prompt_version"] == "template-regions-v1"
    assert data["answers"] == {"Q1": "11 R3", "Q2": "22 R1"}
    assert data.get("metadata", {}).get("source") == "template_regions"
