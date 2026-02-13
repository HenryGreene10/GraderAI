from backend.models.schemas import CriterionScore, GradeResult, QuestionGrade
from backend.services.scoring import parse_quotient_remainder, score_quotient_remainder
from backend.services.template_grader import _build_template_overlay


def test_remainder_ro_normalizes_to_r0():
    status, score, _rationale, low_conf = score_quotient_remainder("123 R0", "123 RO")
    assert status == "correct"
    assert score == 1.0
    assert low_conf is False


def test_remainder_spacing_and_case_variants():
    assert parse_quotient_remainder("45 r 7") == (45, 7)
    status, score, _rationale, low_conf = score_quotient_remainder("45 r 7", "45R7")
    assert status == "correct"
    assert score == 1.0
    assert low_conf is False


def test_remainder_unparseable_needs_review():
    status, score, _rationale, low_conf = score_quotient_remainder("12 R 3", "n/a")
    assert status == "needs_review"
    assert score == 0.0
    assert low_conf is True


def test_remainder_missing_r_fallback():
    assert parse_quotient_remainder("161 03") == (161, 3)


def test_remainder_slash_variant():
    assert parse_quotient_remainder("161/03") == (161, 3)


def test_remainder_single_number_implies_zero_remainder():
    assert parse_quotient_remainder("78") == (78, 0)


def test_remainder_division_expression_is_supported():
    assert parse_quotient_remainder("5)588") == (117, 3)
    status, score, _rationale, low_conf = score_quotient_remainder("5)588", "117 R3")
    assert status == "correct"
    assert score == 1.0
    assert low_conf is False


def test_non_answer_alpha_numeric_label_is_not_parsed_as_number():
    assert parse_quotient_remainder("K-5") is None


def test_remainder_ocr_digit_confusions():
    status, score, _rationale, low_conf = score_quotient_remainder("161 R3", "16l R O3")
    assert status == "correct"
    assert score == 1.0
    assert low_conf is False


def test_remainder_ambiguous_characters_needs_review():
    status, score, _rationale, low_conf = score_quotient_remainder("71 R7", r"7\\ R7")
    assert status == "needs_review"
    assert score == 0.0
    assert low_conf is True


def test_overlay_marks_use_answer_box_bbox():
    grade = GradeResult(
        submission_id="s1",
        total_score=1.0,
        total_max=1.0,
        rubric_version="test",
        prompt_version="test",
        items=[
            QuestionGrade(
                question_id="Q1",
                qtype="short_answer",
                score=1.0,
                max_score=1.0,
                criteria=[CriterionScore(name="test", score=1.0, max_score=1.0, rationale="")],
                rationale="",
                low_confidence=False,
            )
        ],
    )
    regions = [
        {
            "qid": "Q1",
            "answer_box": {"x": 100.0, "y": 200.0, "w": 80.0, "h": 30.0},
        }
    ]
    overlay, placed, skipped_missing, unplaced = _build_template_overlay(regions, grade, (1000, 1000))
    assert placed == 1
    assert skipped_missing == 0
    assert unplaced == []
    assert len(overlay.marks) >= 2  # score bubble + check


def test_overlay_skips_missing_region():
    grade = GradeResult(
        submission_id="s1",
        total_score=0.0,
        total_max=1.0,
        rubric_version="test",
        prompt_version="test",
        items=[
            QuestionGrade(
                question_id="Q2",
                qtype="short_answer",
                score=0.0,
                max_score=1.0,
                criteria=[CriterionScore(name="test", score=0.0, max_score=1.0, rationale="")],
                rationale="",
                low_confidence=False,
            )
        ],
    )
    regions = [
        {
            "qid": "Q1",
            "answer_box": {"x": 100.0, "y": 200.0, "w": 80.0, "h": 30.0},
        }
    ]
    overlay, placed, skipped_missing, unplaced = _build_template_overlay(regions, grade, (1000, 1000))
    assert placed == 0
    assert skipped_missing == 1
    assert unplaced == ["Q2"]
    assert len(overlay.marks) >= 2  # score bubble + fallback mark
    assert any(m.tool == "note" and "Q2:" in (m.text or "") for m in overlay.marks)


def test_overlay_low_confidence_adds_review_note():
    grade = GradeResult(
        submission_id="s1",
        total_score=0.0,
        total_max=1.0,
        rubric_version="test",
        prompt_version="test",
        items=[
            QuestionGrade(
                question_id="Q1",
                qtype="short_answer",
                score=0.0,
                max_score=1.0,
                criteria=[CriterionScore(name="test", score=0.0, max_score=1.0, rationale="")],
                rationale="unclear OCR",
                low_confidence=True,
            )
        ],
    )
    regions = [
        {
            "qid": "Q1",
            "answer_box": {"x": 100.0, "y": 200.0, "w": 80.0, "h": 30.0},
        }
    ]
    overlay, placed, skipped_missing, unplaced = _build_template_overlay(regions, grade, (1000, 1000))
    assert placed == 1
    assert skipped_missing == 0
    assert unplaced == []
    assert any(m.tool == "note" and m.text == "REVIEW" for m in overlay.marks)
