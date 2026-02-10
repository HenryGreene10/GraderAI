from backend.api.uploads import _filter_to_expected_qids, _template_mark_integrity_reasons


def test_filter_to_expected_qids_drops_non_manifest_questions():
    expected = ["Q1", "Q3"]
    observed = {"Q1": "1 R0", "Q2": "2 R0", "Q3": "3 R0"}
    filtered = _filter_to_expected_qids(expected, observed)
    assert list(filtered.keys()) == ["Q1", "Q3"]
    assert filtered["Q1"] == "1 R0"
    assert filtered["Q3"] == "3 R0"


def test_template_mark_integrity_reasons_detects_mismatch():
    reasons = _template_mark_integrity_reasons(
        expected_count=3,
        marks_placed=2,
        marks_skipped_missing=0,
        unplaced_items=[],
    )
    assert any("manifest_mark_count_mismatch" in r for r in reasons)


def test_template_mark_integrity_reasons_detects_missing_and_unplaced():
    reasons = _template_mark_integrity_reasons(
        expected_count=3,
        marks_placed=1,
        marks_skipped_missing=2,
        unplaced_items=["Q2", "Q3"],
    )
    assert any("manifest_missing_marks:2" == r for r in reasons)
    assert any("manifest_unplaced_items:2" == r for r in reasons)
