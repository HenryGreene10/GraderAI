from backend.services.ocr import infer_primary_page_size_px, normalize_ocr_result


def test_normalize_ocr_result_converts_inches_to_pixels():
    raw = {
        "text": "Q1",
        "pages": {
            "analyzeResult": {
                "readResults": [
                    {
                        "unit": "inch",
                        "width": 8.5,
                        "height": 11.0,
                        "lines": [
                            {
                                "text": "4",
                                "boundingBox": [1.0, 1.0, 2.0, 1.0, 2.0, 1.5, 1.0, 1.5],
                                "words": [{"text": "4", "boundingBox": [1.0, 1.0, 2.0, 1.0, 2.0, 1.5, 1.0, 1.5]}],
                            }
                        ],
                    }
                ]
            }
        },
    }
    norm = normalize_ocr_result(raw)
    page0 = norm["boxes"]["analyzeResult"]["readResults"][0]
    assert page0["unit"] == "pixel"
    assert page0["width"] == 2550.0
    assert page0["height"] == 3300.0
    assert page0["lines"][0]["boundingBox"][0] == 300.0
    assert page0["lines"][0]["words"][0]["boundingBox"][4] == 600.0


def test_infer_primary_page_size_px_converts_centimeters_when_unit_missing():
    boxes = {
        "analyzeResult": {
            "readResults": [
                {
                    "width": 24.0,
                    "height": 33.0,
                    "lines": [],
                }
            ]
        }
    }
    w, h = infer_primary_page_size_px(boxes)
    assert round(w) == 2835
    assert round(h) == 3898
