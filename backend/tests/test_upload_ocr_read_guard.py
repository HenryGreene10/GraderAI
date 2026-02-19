from backend.api.uploads import _apply_template_review_guard, _template_ocr_read_audit
from backend.models.schemas import CriterionScore, GradeResult, QuestionGrade


def _item(qid: str, score: float, rationale: str = "", low_confidence: bool = False) -> QuestionGrade:
    return QuestionGrade(
        question_id=qid,
        qtype="short_answer",
        score=score,
        max_score=1.0,
        criteria=[CriterionScore(name="test", score=score, max_score=1.0, rationale=rationale)],
        rationale=rationale,
        low_confidence=low_confidence,
    )


def _grade() -> GradeResult:
    return GradeResult(
        submission_id="u1",
        total_score=1.0,
        total_max=3.0,
        rubric_version="test",
        prompt_version="test",
        items=[
            _item("Q1", 1.0, "Exact quotient/remainder match"),
            _item("Q2", 0.0, "Quotient/remainder mismatch"),
            _item("Q3", 0.0, "Needs review: unable to parse quotient/remainder", low_confidence=True),
        ],
    )


def test_template_ocr_read_audit_triggers_review_all_on_low_coverage():
    audit = _template_ocr_read_audit(
        expected_qids=["Q1", "Q2", "Q3", "Q4"],
        student_answers={"Q1": "12 R0", "Q2": "", "Q3": "", "Q4": ""},
        missing_qids=["Q2", "Q3", "Q4"],
        region_frame_source="aligned_image_ocr",
        region_frame_error=None,
    )
    assert audit["review_all"] is True
    assert "ocr_coverage_low:1/4" in (audit.get("reasons") or [])


def test_template_ocr_read_audit_triggers_review_all_on_degraded_frame_with_missing():
    audit = _template_ocr_read_audit(
        expected_qids=["Q1", "Q2", "Q3"],
        student_answers={"Q1": "12 R0", "Q2": "14 R0", "Q3": ""},
        missing_qids=["Q3"],
        region_frame_source="scaled_ocr_boxes",
        region_frame_error=None,
    )
    assert audit["review_all"] is True
    assert "ocr_degraded_frame:scaled_ocr_boxes" in (audit.get("reasons") or [])


def test_apply_template_review_guard_marks_all_questions_low_confidence_when_review_all():
    grade = _grade()
    changed = _apply_template_review_guard(
        grade,
        review_all=True,
        review_qids=set(),
        reasons=["ocr_coverage_low:1/4"],
    )
    assert changed is True
    assert grade.needs_review is True
    assert all(item.low_confidence for item in grade.items)
    assert all(str(item.rationale).lower().startswith("needs review:") for item in grade.items)


def test_apply_template_review_guard_marks_only_selected_qids_when_not_review_all():
    grade = _grade()
    changed = _apply_template_review_guard(
        grade,
        review_all=False,
        review_qids={"Q2"},
        reasons=["ocr_missing_many:2"],
    )
    assert changed is True
    q1 = next(item for item in grade.items if item.question_id == "Q1")
    q2 = next(item for item in grade.items if item.question_id == "Q2")
    q3 = next(item for item in grade.items if item.question_id == "Q3")
    assert q1.low_confidence is False
    assert q2.low_confidence is True
    assert q3.low_confidence is True
    assert str(q2.rationale).lower().startswith("needs review:")
