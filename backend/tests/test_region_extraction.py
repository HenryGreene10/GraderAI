from backend.services.template_regions import extract_answers_from_regions


def test_extract_answers_from_regions_with_normalized_boxes():
    ocr_boxes = {
        "analyzeResult": {
            "readResults": [
                {
                    "width": 1000,
                    "height": 1000,
                    "lines": [
                        {
                            "words": [
                                {
                                    "text": "123",
                                    "boundingBox": [100, 100, 200, 100, 200, 120, 100, 120],
                                },
                                {
                                    "text": "R7",
                                    "boundingBox": [210, 100, 250, 100, 250, 120, 210, 120],
                                },
                                {
                                    "text": "OUT",
                                    "boundingBox": [800, 800, 900, 800, 900, 820, 800, 820],
                                },
                            ]
                        }
                    ],
                }
            ]
        }
    }
    regions_payload = {
        "version": 1,
        "page_index": 0,
        "template_width_px": 1000,
        "template_height_px": 1000,
        "regions": [
            {"qid": "Q1", "bbox_px": [100, 90, 200, 60]},
            {"qid": "Q2", "bbox_px": [600, 600, 200, 60]},
        ],
    }

    answers, missing = extract_answers_from_regions(ocr_boxes, regions_payload)
    assert answers["Q1"] == "123 R7"
    assert answers["Q2"] == ""
    assert missing == ["Q2"]


def test_extract_answers_prefers_answer_like_text_over_division_expression():
    ocr_boxes = {
        "analyzeResult": {
            "readResults": [
                {
                    "width": 1000,
                    "height": 1000,
                    "lines": [
                        {
                            "words": [
                                {"text": "71", "boundingBox": [320, 200, 360, 200, 360, 230, 320, 230]},
                                {"text": "R7", "boundingBox": [365, 200, 410, 200, 410, 230, 365, 230]},
                            ]
                        },
                        {
                            "words": [
                                {"text": "8)575", "boundingBox": [300, 300, 430, 300, 430, 335, 300, 335]},
                            ]
                        },
                    ],
                }
            ]
        }
    }
    regions_payload = {
        "version": 1,
        "page_index": 0,
        "template_width_px": 1000,
        "template_height_px": 1000,
        "regions": [
            {
                "qid": "Q1",
                "bbox_px": [290, 290, 160, 60],
                "region_box_px": [260, 170, 220, 200],
            }
        ],
    }

    answers, missing = extract_answers_from_regions(ocr_boxes, regions_payload)
    assert answers["Q1"] == "71 R7"
    assert missing == []


def test_extract_answers_recovers_non_adjacent_quotient_remainder_pair():
    ocr_boxes = {
        "analyzeResult": {
            "readResults": [
                {
                    "width": 1000,
                    "height": 1000,
                    "lines": [
                        {
                            "words": [
                                {"text": "171", "boundingBox": [300, 210, 360, 210, 360, 240, 300, 240]},
                                {"text": "x", "boundingBox": [365, 210, 390, 210, 390, 240, 365, 240]},
                                {"text": "R7", "boundingBox": [395, 210, 435, 210, 435, 240, 395, 240]},
                            ]
                        },
                    ],
                }
            ]
        }
    }
    regions_payload = {
        "version": 1,
        "page_index": 0,
        "template_width_px": 1000,
        "template_height_px": 1000,
        "regions": [
            {
                "qid": "Q1",
                "bbox_px": [290, 200, 170, 60],
                "expected_answer_text": "171 R7",
            }
        ],
    }

    answers, missing = extract_answers_from_regions(ocr_boxes, regions_payload)
    assert answers["Q1"] == "171 R7"
    assert missing == []
