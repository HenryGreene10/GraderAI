from backend.services.template_anchor_regions import _anchor_fallback_is_reliable, build_anchor_template_regions
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


def test_anchor_mode_expected_answers_do_not_use_q_labels():
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
                        _line([_word("Q1.", 92, 220, 70, 30), _word("23R0", 470, 222, 90, 30)]),
                        _line(
                            [
                                _word("Q2.", 92, 360, 70, 30),
                                _word("71", 470, 362, 40, 30),
                                _word("R7", 520, 362, 40, 30),
                                _word("Q3.", 700, 360, 70, 30),
                            ]
                        ),
                        _line([_word("Q3.", 92, 500, 70, 30), _word("78", 470, 502, 46, 30), _word("R1", 526, 502, 40, 30)]),
                    ],
                }
            ]
        }
    }
    regions, _warnings, _anchor_trace = build_anchor_template_regions(ocr_boxes=boxes, image_size=(1200, 1600))
    expected_by_qid = {r.qid: str(r.expected_answer_text or "") for r in regions}
    assert expected_by_qid.get("Q2") in {"71 R7", "71R7"}
    assert not str(expected_by_qid.get("Q2") or "").upper().startswith("Q")
    assert all(str(text or "").strip() not in {"", ".", "Q"} for text in expected_by_qid.values())


def test_anchor_mode_does_not_steal_answer_from_later_row():
    boxes = {
        "analyzeResult": {
            "readResults": [
                {
                    "page": 1,
                    "angle": 0.0,
                    "width": 1224,
                    "height": 1584,
                    "unit": "pixel",
                    "lines": [
                        _line([_word("Q1.", 110, 443, 52, 34), _word("23RO", 218, 447, 68, 32)]),
                        _line([_word("Q2.", 421, 439, 45, 38), _word("171R7", 521, 448, 68, 30)]),
                        _line([_word("Q3.", 721, 440, 44, 42), _word("78", 829, 440, 34, 30), _word("R1", 869, 439, 28, 30)]),
                        _line([_word("Q4.", 114, 752, 52, 36), _word("161", 210, 746, 34, 34), _word("R3", 253, 743, 36, 34)]),
                    ],
                }
            ]
        }
    }
    regions, _warnings, _anchor_trace = build_anchor_template_regions(ocr_boxes=boxes, image_size=(1224, 1584))
    expected_by_qid = {r.qid: str(r.expected_answer_text or "") for r in regions}
    q1_text = str(expected_by_qid.get("Q1") or "")
    assert "23" in q1_text
    assert "161" not in q1_text


def test_anchor_mode_assigns_unknown_q_anchor_to_next_number():
    boxes = {
        "analyzeResult": {
            "readResults": [
                {
                    "page": 1,
                    "angle": 0.0,
                    "width": 1224,
                    "height": 1584,
                    "unit": "pixel",
                    "lines": [
                        _line([_word("Q1.", 110, 443, 52, 34), _word("23R0", 218, 447, 68, 32)]),
                        _line([_word("Q2.", 421, 439, 45, 38), _word("171R7", 521, 448, 68, 30)]),
                        _line([_word("Q3.", 721, 440, 44, 42), _word("78", 829, 440, 34, 30), _word("R1", 869, 439, 28, 30)]),
                        _line([_word("Q4.", 114, 752, 52, 36), _word("161", 210, 746, 34, 34), _word("R3", 253, 743, 36, 34)]),
                        _line([_word("Q5.", 420, 744, 52, 36), _word("131", 511, 738, 48, 38), _word("R5", 563, 738, 36, 38)]),
                        _line([_word("Q6.", 720, 745, 49, 34), _word("39", 819, 741, 35, 33), _word("R3", 860, 739, 35, 33)]),
                        _line([_word("Q7.", 105, 1041, 45, 59), _word("17", 530, 1035, 35, 52), _word("R3", 566, 1035, 33, 52)]),
                        _line([_word("Q8.", 415, 1051, 51, 40), _word("117", 610, 1038, 50, 48), _word("R3", 665, 1038, 34, 48)]),
                        _line([_word("Q", 706, 1039, 28, 57), _word("127", 816, 1048, 44, 32), _word("R3", 868, 1046, 33, 32)]),
                    ],
                }
            ]
        }
    }
    regions, warnings, _anchor_trace = build_anchor_template_regions(ocr_boxes=boxes, image_size=(1224, 1584))
    expected_by_qid = {r.qid: str(r.expected_answer_text or "") for r in regions}
    assert "Q9" in expected_by_qid
    assert "127" in str(expected_by_qid.get("Q9") or "")
    codes = {str(w.get("code")) for w in warnings if isinstance(w, dict)}
    assert "ANCHOR_UNKNOWN_NUMBER_FILL" in codes


def test_anchor_mode_ignores_trailing_dot_label_like_answer_text():
    boxes = {
        "analyzeResult": {
            "readResults": [
                {
                    "page": 1,
                    "angle": 0.0,
                    "width": 1224,
                    "height": 1584,
                    "unit": "pixel",
                    "lines": [
                        _line([_word("Q7.", 105, 1041, 50, 56)]),
                        _line([_word("8.", 430, 1046, 36, 48)]),
                        _line([_word("17", 530, 1035, 35, 52), _word("R3", 566, 1035, 33, 52)]),
                    ],
                }
            ]
        }
    }
    regions, _warnings, _anchor_trace = build_anchor_template_regions(ocr_boxes=boxes, image_size=(1224, 1584))
    assert len(regions) == 1
    text = str(regions[0].expected_answer_text or "")
    assert "17" in text
    assert "R3" in text
    assert text.strip() != "8."


def test_anchor_mode_fallback_does_not_steal_footer_k5_text():
    boxes = {
        "analyzeResult": {
            "readResults": [
                {
                    "page": 1,
                    "angle": 0.0,
                    "width": 1224,
                    "height": 1584,
                    "unit": "pixel",
                    "lines": [
                        _line([_word("Q7.", 105, 1041, 50, 56), _word("17", 530, 1035, 35, 52), _word("R3", 566, 1035, 33, 52)]),
                        _line([_word("Q8.", 415, 1051, 51, 40)]),
                        _line([_word("K-5", 350, 1417, 57, 42)]),
                    ],
                }
            ]
        }
    }

    regions, _warnings, _anchor_trace = build_anchor_template_regions(ocr_boxes=boxes, image_size=(1224, 1584))
    by_qid = {r.qid: r for r in regions}
    q8 = by_qid.get("Q8")
    assert q8 is not None
    assert str(q8.expected_answer_text or "").upper() != "K-5"
    # Q8 answer box should stay near the question row, not drift to footer text.
    assert float(q8.answer_box[1]) < 1250.0


def test_anchor_mode_bottom_row_rejects_far_below_numeric_candidate():
    boxes = {
        "analyzeResult": {
            "readResults": [
                {
                    "page": 1,
                    "angle": 0.0,
                    "width": 1224,
                    "height": 1584,
                    "unit": "pixel",
                    "lines": [
                        _line([_word("Q7.", 105, 1041, 50, 56)]),
                        _line([_word("76", 534, 1220, 40, 44)]),
                    ],
                }
            ]
        }
    }
    regions, _warnings, _trace = build_anchor_template_regions(ocr_boxes=boxes, image_size=(1224, 1584))
    assert len(regions) == 1
    assert str(regions[0].expected_answer_text or "").strip() == ""
    # Bottom-row fallback should stay near Q7, not drift to a far-lower numeric token.
    assert float(regions[0].answer_box[1]) < 1125.0


def test_anchor_mode_left_column_can_reach_far_right_answer_span():
    boxes = {
        "analyzeResult": {
            "readResults": [
                {
                    "page": 1,
                    "angle": 0.0,
                    "width": 1224,
                    "height": 1584,
                    "unit": "pixel",
                    "lines": [
                        _line([_word("Q7.", 105, 1041, 45, 59), _word("17", 530, 1035, 35, 52), _word("R3", 566, 1035, 33, 52)]),
                    ],
                }
            ]
        }
    }
    regions, _warnings, _trace = build_anchor_template_regions(ocr_boxes=boxes, image_size=(1224, 1584))
    assert len(regions) == 1
    assert regions[0].qid == "Q7"
    assert "17" in str(regions[0].expected_answer_text or "")
    # Ensure this is a detected answer span, not a tiny fallback box near the label.
    assert float(regions[0].answer_box[0]) > 300.0


def test_anchor_mode_fallback_box_is_bounded_when_answer_missing():
    boxes = {
        "analyzeResult": {
            "readResults": [
                {
                    "page": 1,
                    "angle": 0.0,
                    "width": 1224,
                    "height": 1584,
                    "unit": "pixel",
                    "lines": [
                        _line([_word("Q7.", 105, 1041, 45, 59)]),
                    ],
                }
            ]
        }
    }
    regions, _warnings, _trace = build_anchor_template_regions(ocr_boxes=boxes, image_size=(1224, 1584))
    assert len(regions) == 1
    # Missing-answer fallback should remain compact/stable for overlay anchoring.
    assert float(regions[0].answer_box[3]) < 72.0


def test_box_driven_mode_uses_box_count_as_question_count():
    lines = []
    for idx in range(2, 10):
        y = 220 + (idx - 2) * 120
        lines.append(_line([_word(f"Q{idx}.", 92, y, 70, 30)]))
        lines.append(_line([_word(str(10 + idx), 520, y + 2, 46, 30)]))
    lines.append(_line([_word("99", 520, 1172, 46, 30)]))
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
    assert "BOX_MISSING_Q_NUMBERS" in codes
    assert "BOX_ROW_FALLBACK_QIDS" in codes


def test_box_driven_mode_flags_box_count_too_many():
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
                        _line([_word("Q2.", 96, 360, 70, 30), _word("14", 480, 362, 46, 30)]),
                    ],
                }
            ]
        }
    }
    hints = [
        (460.0, 210.0, 74.0, 38.0),
        (460.0, 350.0, 74.0, 38.0),
        (700.0, 210.0, 74.0, 38.0),
    ]
    _regions, warnings, _trace = build_anchor_template_regions(
        ocr_boxes=boxes,
        image_size=(1000, 1400),
        answer_box_hints=hints,
    )
    codes = {str(w.get("code")) for w in warnings if isinstance(w, dict)}
    assert "BOX_COUNT_TOO_MANY" in codes


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


def test_box_driven_mode_filters_q_label_and_division_boxes():
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
                        _line([_word("Q1.", 96, 220, 70, 30), _word("12", 480, 222, 46, 30)]),
                        _line([_word("Q2.", 96, 360, 70, 30), _word("14", 480, 362, 46, 30)]),
                        _line([_word("7)780", 180, 520, 120, 34)]),
                    ],
                }
            ]
        }
    }
    hints = [
        (460.0, 210.0, 74.0, 38.0),  # answer box
        (460.0, 350.0, 74.0, 38.0),  # answer box
        (90.0, 210.0, 82.0, 40.0),   # q-label bubble/box false positive
        (170.0, 510.0, 140.0, 44.0), # division expression false positive
    ]
    regions, warnings, anchor_trace = build_anchor_template_regions(
        ocr_boxes=boxes,
        image_size=(1200, 1600),
        answer_box_hints=hints,
    )
    assert len(regions) == 2
    codes = {str(w.get("code")) for w in warnings if isinstance(w, dict)}
    assert "BOX_CANDIDATES_FILTERED" in codes
    filtered = anchor_trace.get("filtered") or []
    assert len(filtered) >= 2


def test_box_driven_mode_falls_back_to_anchor_mode_on_low_coverage():
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
                        _line([_word("Q1.", 96, 220, 70, 30), _word("12", 480, 222, 46, 30)]),
                        _line([_word("Q2.", 96, 360, 70, 30), _word("14", 480, 362, 46, 30)]),
                        _line([_word("Q3.", 96, 500, 70, 30), _word("16", 480, 502, 46, 30)]),
                        _line([_word("Q4.", 96, 640, 70, 30), _word("18", 480, 642, 46, 30)]),
                    ],
                }
            ]
        }
    }
    hints = [
        (460.0, 210.0, 74.0, 38.0),
        (460.0, 350.0, 74.0, 38.0),
    ]
    regions, warnings, anchor_trace = build_anchor_template_regions(
        ocr_boxes=boxes,
        image_size=(1200, 1600),
        answer_box_hints=hints,
    )
    qids = [r.qid for r in regions]
    assert qids == ["Q1", "Q2", "Q3", "Q4"]
    codes = {str(w.get("code")) for w in warnings if isinstance(w, dict)}
    assert "BOX_MODE_FALLBACK_APPLIED" in codes
    assert isinstance(anchor_trace.get("box_mode_discarded"), dict)


def test_anchor_fallback_reliability_flags_duplicate_and_fallback_numbering():
    assert _anchor_fallback_is_reliable([]) is True
    assert (
        _anchor_fallback_is_reliable(
            [
                {"code": "ANCHOR_DUPLICATE_NUMBERS"},
            ]
        )
        is False
    )
    assert (
        _anchor_fallback_is_reliable(
            [
                {"code": "ANCHOR_NUMBER_FALLBACK_ORDER"},
            ]
        )
        is False
    )


def test_box_driven_mode_keeps_box_regions_when_anchor_fallback_is_ambiguous():
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
                        _line([_word("Q1.", 96, 220, 70, 30), _word("12", 480, 222, 46, 30)]),
                        _line([_word("Q1.", 96, 360, 70, 30), _word("14", 480, 362, 46, 30)]),
                        _line([_word("Q.", 96, 500, 50, 30), _word("16", 480, 502, 46, 30)]),
                        _line([_word("Q4.", 96, 640, 70, 30), _word("18", 480, 642, 46, 30)]),
                    ],
                }
            ]
        }
    }
    hints = [
        (460.0, 210.0, 74.0, 38.0),
        (460.0, 350.0, 74.0, 38.0),
    ]
    regions, warnings, anchor_trace = build_anchor_template_regions(
        ocr_boxes=boxes,
        image_size=(1200, 1600),
        answer_box_hints=hints,
    )

    assert len(regions) == 2
    codes = {str(w.get("code")) for w in warnings if isinstance(w, dict)}
    assert "BOX_MODE_FALLBACK_APPLIED" not in codes
    rejected = anchor_trace.get("anchor_mode_rejected") or {}
    assert rejected.get("reason") == "anchor_fallback_not_reliable"
    assert "ANCHOR_DUPLICATE_NUMBERS" in (rejected.get("anchor_warning_codes") or [])


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
