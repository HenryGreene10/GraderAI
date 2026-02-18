from backend.services.master_key_pipeline import (
    _blocking_and_softened_reasons,
    _append_expected_text_quality_warning,
    _blocking_reasons_from_warning_codes,
    _is_low_quality_expected_text,
)


def test_is_low_quality_expected_text_flags_labelish_and_div_expr():
    assert _is_low_quality_expected_text("")
    assert _is_low_quality_expected_text("Q3.")
    assert _is_low_quality_expected_text(".")
    assert _is_low_quality_expected_text("5)588")
    assert not _is_low_quality_expected_text("171 R7")


def test_append_expected_text_quality_warning_collects_bad_qids():
    payload = {
        "regions": [
            {"qid": "Q1", "expected_answer_text": "171 R7"},
            {"qid": "Q2", "expected_answer_text": "Q3."},
            {"qid": "Q3", "expected_answer_text": ""},
        ]
    }
    warnings: list[dict[str, object]] = []
    _append_expected_text_quality_warning(payload, warnings)
    assert len(warnings) == 1
    assert warnings[0].get("code") == "EXPECTED_TEXT_QUALITY_LOW"
    assert warnings[0].get("qids") == ["Q2", "Q3"]


def test_blocking_reasons_include_fallback_and_quality_low():
    warning_codes = {"BOX_MODE_FALLBACK_APPLIED", "EXPECTED_TEXT_QUALITY_LOW", "IGNORED"}
    reasons = _blocking_reasons_from_warning_codes(warning_codes)
    assert "BOX_MODE_FALLBACK_APPLIED" in reasons
    assert "EXPECTED_TEXT_QUALITY_LOW" in reasons


def test_soft_blockers_are_tolerated_when_coverage_is_strong():
    warning_codes = {"BOX_MODE_FALLBACK_APPLIED", "ANCHOR_AMBIGUITY_HIGH", "EXPECTED_TEXT_QUALITY_LOW"}
    warnings = [
        {
            "code": "BOX_MODE_FALLBACK_APPLIED",
            "box_regions": 4,
            "anchor_regions": 9,
        },
        {
            "code": "ANCHOR_AMBIGUITY_HIGH",
            "anchor_count": 10,
            "unknown_count": 1,
            "duplicate_numbers": [4],
            "out_of_range_numbers": [],
        },
        {
            "code": "EXPECTED_TEXT_QUALITY_LOW",
            "count": 1,
            "qids": ["Q8"],
        },
    ]
    blocked, softened = _blocking_and_softened_reasons(
        warning_codes,
        warnings=warnings,
        regions_total=9,
    )
    assert blocked == []
    assert softened == [
        "ANCHOR_AMBIGUITY_HIGH",
        "BOX_MODE_FALLBACK_APPLIED",
        "EXPECTED_TEXT_QUALITY_LOW",
    ]


def test_soft_blockers_still_block_when_severity_is_high():
    warning_codes = {"BOX_MODE_FALLBACK_APPLIED", "ANCHOR_AMBIGUITY_HIGH", "EXPECTED_TEXT_QUALITY_LOW"}
    warnings = [
        {
            "code": "BOX_MODE_FALLBACK_APPLIED",
            "box_regions": 3,
            "anchor_regions": 4,
        },
        {
            "code": "ANCHOR_AMBIGUITY_HIGH",
            "anchor_count": 5,
            "unknown_count": 2,
            "duplicate_numbers": [2, 4],
            "out_of_range_numbers": [12],
        },
        {
            "code": "EXPECTED_TEXT_QUALITY_LOW",
            "count": 3,
            "qids": ["Q3", "Q7", "Q8"],
        },
    ]
    blocked, softened = _blocking_and_softened_reasons(
        warning_codes,
        warnings=warnings,
        regions_total=9,
    )
    assert blocked == [
        "ANCHOR_AMBIGUITY_HIGH",
        "BOX_MODE_FALLBACK_APPLIED",
        "EXPECTED_TEXT_QUALITY_LOW",
    ]
    assert softened == []
