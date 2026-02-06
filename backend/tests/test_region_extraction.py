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
        "size_px": [1000, 1000],
        "regions": {
            "Q1": {
                "answer_box": {"x": 0.1, "y": 0.09, "w": 0.2, "h": 0.06},
            },
            "Q2": {
                "answer_box": {"x": 0.6, "y": 0.6, "w": 0.2, "h": 0.06},
            },
        },
    }

    answers, missing = extract_answers_from_regions(ocr_boxes, regions_payload)
    assert answers["Q1"] == "123 R7"
    assert answers["Q2"] == ""
    assert missing == ["Q2"]
