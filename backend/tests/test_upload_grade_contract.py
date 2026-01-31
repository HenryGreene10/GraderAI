from fastapi.testclient import TestClient

from backend.app import app
from backend.models.schemas import CriterionScore, GradeResult, QuestionGrade
from backend.services.llm_grader import LLMAnswer


def _auth_headers(user_id="owner-1"):
    return {"Authorization": f"Bearer user:{user_id}"}


def test_upload_grade_sets_pdf(fake_supabase, monkeypatch):
    rows = fake_supabase._db["uploads"]
    rows["u9"] = {
        "id": "u9",
        "owner_id": "owner-1",
        "storage_path": "submissions/owner-1/file.pdf",
        "ocr_status": "done",
        "ocr_text": "Q1: 2+2? 4",
        "ocr_boxes": {
            "analyzeResult": {
                "readResults": [
                    {
                        "width": 1000,
                        "height": 1000,
                        "lines": [
                            {
                                "text": "4",
                                "boundingBox": [10, 10, 20, 10, 20, 20, 10, 20],
                                "appearance": {"style": {"name": "handwriting"}},
                            }
                        ],
                    }
                ]
            }
        },
        "mime_type": "application/pdf",
        "normalized_width_px": 3000,
        "normalized_height_px": 4000,
    }
    fake_supabase.storage.objects[("submissions", "owner-1/file.pdf")] = b"%PDF-1.4 mock"

    async def fake_grade_with_llm(_text):
        item = QuestionGrade(
            question_id="1",
            qtype="short_answer",
            score=1.0,
            max_score=1.0,
            criteria=[CriterionScore(name="llm", score=1.0, max_score=1.0, rationale="ok")],
            rationale="ok",
            low_confidence=False,
        )
        result = GradeResult(
            submission_id="u9",
            total_score=1.0,
            total_max=1.0,
            items=[item],
            rubric_version="single-pass",
            prompt_version="single-pass-v1",
            needs_review=False,
        )
        answers = [
            LLMAnswer(
                question_id="1",
                question="2+2?",
                student_answer="4",
                correct=True,
                confidence=0.9,
                rationale="basic",
            )
        ]
        return result, answers

    monkeypatch.setattr("backend.api.uploads.grade_with_llm", fake_grade_with_llm)
    monkeypatch.setattr("backend.api.uploads.get_page_sizes", lambda *_args, **_kwargs: [(612.0, 792.0)])
    seen = {}

    def fake_render_marked_pdf(_bytes, _mime, overlay, **_kwargs):
        texts = [m.text for m in (overlay.marks if overlay else []) if m.text]
        assert any("Score:" in t for t in texts)
        assert _kwargs.get("normalized_size_px") == (3000.0, 4000.0)
        seen["overlay_used"] = True
        return b"%PDF-1.4\n%mock"

    monkeypatch.setattr("backend.api.uploads.render_marked_pdf", fake_render_marked_pdf)

    client = TestClient(app)
    resp = client.post("/api/uploads/u9/grade", headers=_auth_headers())
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["graded_pdf_path"] == "owner-1/u9.pdf"

    assert rows["u9"]["graded_pdf_path"] == "owner-1/u9.pdf"
    assert rows["u9"]["grade_json"]
    assert rows["u9"]["overlay_json"]
    assert rows["u9"]["overlay_path"] == "owner-1/u9.json"
    assert ("overlays", "owner-1/u9.json") in fake_supabase.storage.objects
    assert seen.get("overlay_used") is True
    assert ("graded-pdfs", "owner-1/u9.pdf") in fake_supabase.storage.objects
