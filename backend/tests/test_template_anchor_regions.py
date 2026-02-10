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
