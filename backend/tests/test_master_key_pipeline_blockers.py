from backend.services.master_key_pipeline import (
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
