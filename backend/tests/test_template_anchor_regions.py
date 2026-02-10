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
    regions, warnings = build_anchor_template_regions(
        ocr_boxes=_k5_like_ocr_boxes(),
        image_size=(1200, 1600),
    )
    assert regions, warnings
    assert len(regions) == 9

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
    regions, warnings = build_anchor_template_regions(ocr_boxes=boxes, image_size=(1000, 1400))
    assert regions, warnings
    assert len(regions) == 2
    assert [r.qid for r in regions] == ["Q1", "Q2"]
    assert all(r.answer_box[2] > 0 and r.answer_box[3] > 0 for r in regions)


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
    regions, warnings = build_anchor_template_regions(ocr_boxes=boxes, image_size=(1100, 1500))
    assert regions, warnings
    assert len(regions) == 2
    assert [r.qid for r in regions] == ["Q1", "Q2"]
    assert all(r.answer_box[2] > 0 and r.answer_box[3] > 0 for r in regions)


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
    regions, warnings = build_anchor_template_regions(ocr_boxes=boxes, image_size=(1200, 1600))
    qids = [r.qid for r in regions]
    assert qids == ["Q2", "Q3", "Q4", "Q5", "Q6", "Q7", "Q8", "Q9"]
    codes = {str(w.get("code")) for w in warnings if isinstance(w, dict)}
    assert "ANCHOR_COVERAGE_BELOW_THRESHOLD" in codes


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
    regions, warnings = build_anchor_template_regions(ocr_boxes=boxes, image_size=(1000, 1400))
    assert len(regions) == 2
    a = regions[0].answer_box
    b = regions[1].answer_box
    assert not (abs(a[0] - b[0]) < 1e-6 and abs(a[1] - b[1]) < 1e-6 and abs(a[2] - b[2]) < 1e-6 and abs(a[3] - b[3]) < 1e-6)
