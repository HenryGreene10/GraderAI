from backend.models.schemas import CriterionScore, GradeResult, QuestionGrade
from backend.services.template_regions import build_overlay_from_regions


def _question(qid: str, score: float, max_score: float = 1.0, low_conf: bool = False) -> QuestionGrade:
    return QuestionGrade(
        question_id=qid,
        qtype="short_answer",
        score=score,
        max_score=max_score,
        criteria=[CriterionScore(name="test", score=score, max_score=max_score, rationale="")],
        rationale="",
        low_confidence=low_conf,
    )


def test_template_regions_overlay_adds_fallback_for_unplaced_items():
    payload = {
        "version": 1,
        "page_index": 0,
        "template_width_px": 1000,
        "template_height_px": 1000,
        "regions": [{"qid": "Q1", "bbox_px": [100, 100, 120, 40]}],
    }
    grade = GradeResult(
        submission_id="s1",
        total_score=1.0,
        total_max=2.0,
        rubric_version="test",
        prompt_version="test",
        items=[_question("Q1", 1.0), _question("Q2", 0.0)],
    )

    overlay, placed, skipped_missing, skipped_review, unplaced = build_overlay_from_regions(
        grade,
        payload,
        (1000.0, 1000.0),
        (612.0, 792.0),
    )

    assert placed == 1
    assert skipped_missing == 1
    assert skipped_review == 0
    assert unplaced == ["Q2"]
    assert any(m.tool == "note" and "Q2:" in (m.text or "") for m in overlay.marks)


def test_template_regions_overlay_uses_page_specific_sizes():
    payload = {
        "version": 1,
        "page_index": 0,
        "template_width_px": 1000,
        "template_height_px": 1000,
        "regions": [
            {"qid": "Q2", "page_index": 1, "bbox_px": [100, 100, 100, 50]},
        ],
    }
    grade = GradeResult(
        submission_id="s2",
        total_score=1.0,
        total_max=1.0,
        rubric_version="test",
        prompt_version="test",
        items=[_question("Q2", 1.0)],
    )

    overlay, placed, skipped_missing, skipped_review, unplaced = build_overlay_from_regions(
        grade,
        payload,
        (1000.0, 1000.0),
        (612.0, 792.0),
        page_sizes_pt=[(612.0, 792.0), (300.0, 300.0)],
    )

    assert placed == 1
    assert skipped_missing == 0
    assert skipped_review == 0
    assert unplaced == []
    meta = overlay.meta or {}
    page2 = (meta.get("marks_by_page") or {}).get("2") or []
    assert page2
    check = next((m for m in page2 if m.get("tool") in {"check", "cross", "note"}), None)
    assert check is not None
    x_pt, y_pt = check["coords"][0], check["coords"][1]
    assert round(x_pt, 1) == 54.6
    assert round(y_pt, 1) == 268.2
