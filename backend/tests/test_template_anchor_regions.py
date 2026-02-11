from backend.services.template_anchor_regions import build_anchor_template_regions
from backend.services.template_manifest import with_approved_manifest
from backend.services.template_regions import build_template_regions_payload


def _bbox(x: float, y: float, w: float, h: float) -> list[float]:
    return [x, y, x + w, y, x + w, y + h, x, y + h]


def _word(text: str, x: float, y: float, w: float, h: float) -> dict:
    return {"text": text, "boundingBox": _bbox(x, y, w, h)}


def _line(words: list[dict]) -> dict:
    x0 = min(w["boundingBox"][0] for w in words)
    y0 = min(w["boundingBox"][1] for w in words)
    x1 = max(w["boundingBox"][4] for w in words)
    y1 = max(w["boundingBox"][5] for w in words)
    return {
        "text": " ".join(w["text"] for w in words),
        "boundingBox": _bbox(x0, y0, x1 - x0, y1 - y0),
        "words": words,
    }


def _k5_like_ocr_boxes() -> dict:
    labels = [
        ["Q1."],
        ["Q", "2."],
        ["(Q3."],
        ["Q4"],
        ["Q5"],
        ["6."],
        ["(Q7)"],
        ["Q", "8"],
        ["Q9."],
    ]
    answers = [
        ["12"],
        ["14"],
        ["16"],
        ["18"],
        ["20"],
        ["11", "R2"],
        ["24"],
        ["26"],
        ["28"],
    ]

    lines = [
        _line([_word("K5", 80, 40, 40, 24), _word("Math", 130, 40, 68, 24), _word("Worksheet", 208, 40, 140, 24)]),
    ]
    for idx in range(9):
        y = 170 + idx * 140
        label_words = []
        lx = 82
        for token in labels[idx]:
            w = 26 + len(token) * 9
            label_words.append(_word(token, lx, y, w, 30))
            lx += w + 10
        lines.append(_line(label_words))

        answer_words = []
        ax = 470
        for token in answers[idx]:
            w = 26 + len(token) * 9
            answer_words.append(_word(token, ax, y + 2, w, 30))
            ax += w + 8
        lines.append(_line(answer_words))

    return {
        "analyzeResult": {
            "readResults": [
                {
                    "page": 1,
                    "angle": 0.0,
                    "width": 1200,
                    "height": 1600,
                    "unit": "pixel",
                    "lines": lines,
                }
            ]
        }
    }


def test_k5_anchor_regions_build_manifest_with_nine_questions():
    regions, warnings, anchor_trace = build_anchor_template_regions(
        ocr_boxes=_k5_like_ocr_boxes(),
        image_size=(1200, 1600),
    )
    assert regions, warnings
    assert len(regions) == 9
    assert isinstance(anchor_trace, dict)
    assert anchor_trace.get("summary", {}).get("candidate_count", 0) >= 9

    payload = build_template_regions_payload(regions, (1200, 1600))
    approved = with_approved_manifest(
        payload,
        template_version=1,
        template_width_px=1200,
        template_height_px=1600,
        approved_at="2026-02-10T00:00:00Z",
    )

    manifest = approved.get("manifest") or {}
    questions = manifest.get("questions") or []
    assert manifest.get("question_count") == 9
    assert len(questions) == 9
    for q in questions:
        box = q.get("answer_box_px") or []
        assert isinstance(box, list) and len(box) == 4
        assert float(box[2]) > 0
        assert float(box[3]) > 0


def test_anchor_detection_handles_two_question_labels_on_same_ocr_line():
    boxes = {
        "analyzeResult": {
            "readResults": [
                {
                    "page": 1,
                    "angle": 0.0,
                    "width": 1000,
                    "height": 1400,
                    "unit": "pixel",
                    "lines": [
                        _line(
                            [
                                _word("Q1.", 90, 200, 70, 30),
                                _word("12", 420, 202, 46, 30),
                                _word("Q2.", 560, 200, 70, 30),
                                _word("14", 860, 202, 46, 30),
                            ]
                        )
                    ],
                }
            ]
        }
    }
    regions, warnings, anchor_trace = build_anchor_template_regions(ocr_boxes=boxes, image_size=(1000, 1400))
    assert regions, warnings
    assert len(regions) == 2
    assert [r.qid for r in regions] == ["Q1", "Q2"]
    assert all(r.answer_box[2] > 0 and r.answer_box[3] > 0 for r in regions)
    assert anchor_trace.get("summary", {}).get("selected_count") == 2


def test_anchor_detection_handles_split_q_and_digit_tokens():
    boxes = {
        "analyzeResult": {
            "readResults": [
                {
                    "page": 1,
                    "angle": 0.0,
                    "width": 1100,
                    "height": 1500,
                    "unit": "pixel",
                    "lines": [
                        _line([_word("Q1.", 96, 210, 74, 30), _word("8", 460, 212, 32, 30)]),
                        _line([_word("Q", 98, 360, 28, 30), _word("2)", 140, 360, 42, 30), _word("11", 480, 362, 40, 30)]),
                    ],
                }
            ]
        }
    }
    regions, warnings, anchor_trace = build_anchor_template_regions(ocr_boxes=boxes, image_size=(1100, 1500))
    assert regions, warnings
    assert len(regions) == 2
    assert [r.qid for r in regions] == ["Q1", "Q2"]
    assert all(r.answer_box[2] > 0 and r.answer_box[3] > 0 for r in regions)
    assert anchor_trace.get("summary", {}).get("selected_count") == 2


def test_explicit_numbers_are_not_renumbered_and_missing_q1_is_flagged():
    lines = []
    for idx in range(2, 10):
        y = 140 + (idx - 2) * 130
        lines.append(_line([_word(f"Q{idx}.", 90, y, 74, 30)]))
        lines.append(_line([_word(str(10 + idx), 480, y + 2, 46, 30)]))

    boxes = {
        "analyzeResult": {
            "readResults": [
                {
                    "page": 1,
                    "angle": 0.0,
                    "width": 1200,
                    "height": 1600,
                    "unit": "pixel",
                    "lines": lines,
                }
            ]
        }
    }
    regions, warnings, anchor_trace = build_anchor_template_regions(ocr_boxes=boxes, image_size=(1200, 1600))
    qids = [r.qid for r in regions]
    assert qids == ["Q2", "Q3", "Q4", "Q5", "Q6", "Q7", "Q8", "Q9"]
    codes = {str(w.get("code")) for w in warnings if isinstance(w, dict)}
    assert "ANCHOR_COVERAGE_BELOW_THRESHOLD" in codes
    assert anchor_trace.get("summary", {}).get("selected_count") == 8
    assert anchor_trace.get("missing_numbers") == [1]


def test_token_reservation_avoids_reusing_same_answer_span():
    boxes = {
        "analyzeResult": {
            "readResults": [
                {
                    "page": 1,
                    "angle": 0.0,
                    "width": 1000,
                    "height": 1400,
                    "unit": "pixel",
                    "lines": [
                        _line([_word("Q1.", 90, 210, 70, 30)]),
                        _line([_word("Q2.", 92, 280, 70, 30)]),
                        _line([_word("42", 500, 245, 46, 30)]),
                    ],
                }
            ]
        }
    }
    regions, warnings, anchor_trace = build_anchor_template_regions(ocr_boxes=boxes, image_size=(1000, 1400))
    assert len(regions) == 2
    a = regions[0].answer_box
    b = regions[1].answer_box
    assert not (abs(a[0] - b[0]) < 1e-6 and abs(a[1] - b[1]) < 1e-6 and abs(a[2] - b[2]) < 1e-6 and abs(a[3] - b[3]) < 1e-6)
    assert isinstance(anchor_trace.get("rejected"), list)


def test_hard_gate_rejects_header_distractor_without_nearby_answer_pair():
    boxes = {
        "analyzeResult": {
            "readResults": [
                {
                    "page": 1,
                    "angle": 0.0,
                    "width": 1200,
                    "height": 1600,
                    "unit": "pixel",
                    "lines": [
                        _line([_word("1.", 84, 42, 30, 24), _word("Score", 130, 42, 72, 24)]),
                        _line([_word("Q1.", 92, 220, 70, 30), _word("12", 480, 222, 46, 30)]),
                        _line([_word("Q2.", 92, 360, 70, 30), _word("14", 480, 362, 46, 30)]),
                    ],
                }
            ]
        }
    }
    regions, warnings, anchor_trace = build_anchor_template_regions(ocr_boxes=boxes, image_size=(1200, 1600))
    assert [r.qid for r in regions] == ["Q1", "Q2"]
    rejected = anchor_trace.get("rejected") or []
    assert any(
        item.get("text") == "1." and item.get("reason") == "hard_gate_no_answer_pair"
        for item in rejected
        if isinstance(item, dict)
    )


def test_targeted_recovery_prefers_expected_row_for_missing_q1():
    boxes = {
        "analyzeResult": {
            "readResults": [
                {
                    "page": 1,
                    "angle": 0.0,
                    "width": 1200,
                    "height": 1600,
                    "unit": "pixel",
                    "lines": [
                        _line([_word("1.", 88, 42, 30, 24), _word("99", 480, 44, 46, 30)]),
                        _line([_word("1.", 90, 220, 30, 24), _word("12", 480, 222, 46, 30)]),
                        _line([_word("Q2.", 92, 360, 70, 30), _word("14", 480, 362, 46, 30)]),
                        _line([_word("Q3.", 92, 500, 70, 30), _word("16", 480, 502, 46, 30)]),
                    ],
                }
            ]
        }
    }

    regions, warnings, anchor_trace = build_anchor_template_regions(ocr_boxes=boxes, image_size=(1200, 1600))
    assert [r.qid for r in regions] == ["Q1", "Q2", "Q3"]
    q1 = next((item for item in (anchor_trace.get("selected") or []) if item.get("question_id") == "Q1"), None)
    assert q1 is not None
    assert float((q1.get("bbox_px") or [0, 0, 0, 0])[1]) >= 180.0


def test_box_driven_mode_uses_box_count_as_question_count():
    lines = []
    for idx in range(2, 10):
        y = 220 + (idx - 2) * 120
        lines.append(_line([_word(f"Q{idx}.", 92, y, 70, 30)]))
        lines.append(_line([_word(str(10 + idx), 520, y + 2, 46, 30)]))
    boxes = {
        "analyzeResult": {
            "readResults": [
                {
                    "page": 1,
                    "angle": 0.0,
                    "width": 1200,
                    "height": 1600,
                    "unit": "pixel",
                    "lines": lines,
                }
            ]
        }
    }
    hints = []
    for i in range(9):
        y = 210 + i * 120
        hints.append((500.0, float(y), 70.0, 36.0))
    regions, warnings, anchor_trace = build_anchor_template_regions(
        ocr_boxes=boxes,
        image_size=(1200, 1600),
        answer_box_hints=hints,
    )
    assert len(regions) == 9
    assert anchor_trace.get("summary", {}).get("selected_count") == 8
    assert anchor_trace.get("missing_numbers") == [1]
    rows = anchor_trace.get("rows") or []
    assert len(rows) == 9
    assert isinstance(rows[0].get("box_bbox_px"), list)
    assert isinstance(rows[0].get("roi_bbox_px"), list)
    codes = {str(w.get("code")) for w in warnings if isinstance(w, dict)}
    assert "BOX_UNREADABLE_Q_LABEL_ROWS" in codes


def test_box_driven_mode_flags_duplicate_q_numbers():
    boxes = {
        "analyzeResult": {
            "readResults": [
                {
                    "page": 1,
                    "angle": 0.0,
                    "width": 1000,
                    "height": 1400,
                    "unit": "pixel",
                    "lines": [
                        _line([_word("Q1.", 96, 220, 70, 30), _word("12", 480, 222, 46, 30)]),
                        _line([_word("Q1.", 96, 360, 70, 30), _word("14", 480, 362, 46, 30)]),
                    ],
                }
            ]
        }
    }
    hints = [
        (460.0, 210.0, 74.0, 38.0),
        (460.0, 350.0, 74.0, 38.0),
    ]
    _regions, warnings, _trace = build_anchor_template_regions(
        ocr_boxes=boxes,
        image_size=(1000, 1400),
        answer_box_hints=hints,
    )
    codes = {str(w.get("code")) for w in warnings if isinstance(w, dict)}
    assert "BOX_DUPLICATE_Q_NUMBERS" in codes


def test_box_driven_mode_reserves_anchor_token_across_rows():
    boxes = {
        "analyzeResult": {
            "readResults": [
                {
                    "page": 1,
                    "angle": 0.0,
                    "width": 1000,
                    "height": 1400,
                    "unit": "pixel",
                    "lines": [
                        _line([_word("Q7.", 110, 320, 70, 30), _word("18", 520, 322, 44, 30)]),
                    ],
                }
            ]
        }
    }
    hints = [
        (500.0, 300.0, 74.0, 38.0),
        (500.0, 340.0, 74.0, 38.0),
    ]
    regions, warnings, anchor_trace = build_anchor_template_regions(
        ocr_boxes=boxes,
        image_size=(1000, 1400),
        answer_box_hints=hints,
    )
    assert len(regions) == 2
    selected = anchor_trace.get("selected") or []
    parsed_nums = [item.get("parsed_num") for item in selected if isinstance(item, dict)]
    assert parsed_nums.count(7) == 1
    rejected = anchor_trace.get("rejected") or []
    assert any(
        item.get("reason") == "anchor_reserved_by_previous_row"
        for item in rejected
        if isinstance(item, dict)
    )
