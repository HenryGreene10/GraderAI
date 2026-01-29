from io import BytesIO

import pytest

cv2 = pytest.importorskip("cv2")
import numpy as np

from backend.services.template import (
    detect_answer_boxes,
    detect_question_regions,
    extract_template_regions,
    TemplateValidationError,
)


def _draw_dashed_rect(img, rect, color=(0, 0, 0), thickness=2, dash=12, gap=8):
    x0, y0, x1, y1 = rect
    for x in range(x0, x1, dash + gap):
        cv2.line(img, (x, y0), (min(x + dash, x1), y0), color, thickness)
        cv2.line(img, (x, y1), (min(x + dash, x1), y1), color, thickness)
    for y in range(y0, y1, dash + gap):
        cv2.line(img, (x0, y), (x0, min(y + dash, y1)), color, thickness)
        cv2.line(img, (x1, y), (x1, min(y + dash, y1)), color, thickness)


def _make_template() -> bytes:
    img = np.full((1000, 800, 3), 255, dtype=np.uint8)
    regions = [
        (60, 80, 740, 260),
        (60, 320, 740, 500),
        (60, 560, 740, 740),
    ]
    for rect in regions:
        _draw_dashed_rect(img, rect, thickness=2)
    # answer boxes (solid thick)
    answer_boxes = [
        (560, 140, 720, 200),
        (560, 380, 720, 440),
        (560, 620, 720, 680),
    ]
    for box in answer_boxes:
        cv2.rectangle(img, (box[0], box[1]), (box[2], box[3]), (0, 0, 0), 5)
    buf = BytesIO()
    from PIL import Image

    Image.fromarray(img).save(buf, format="PNG")
    return buf.getvalue()


def _make_template_with_double_answer() -> bytes:
    img = np.full((1000, 800, 3), 255, dtype=np.uint8)
    regions = [
        (60, 80, 740, 260),
        (60, 320, 740, 500),
    ]
    for rect in regions:
        _draw_dashed_rect(img, rect, thickness=2)
    answer_boxes = [
        (560, 140, 720, 200),
        (420, 150, 520, 210),
        (560, 380, 720, 440),
    ]
    for box in answer_boxes:
        cv2.rectangle(img, (box[0], box[1]), (box[2], box[3]), (0, 0, 0), 5)
    buf = BytesIO()
    from PIL import Image

    Image.fromarray(img).save(buf, format="PNG")
    return buf.getvalue()


def _make_boxes_only() -> bytes:
    img = np.full((900, 700, 3), 255, dtype=np.uint8)
    answer_boxes = [
        (480, 160, 620, 220),
        (480, 420, 620, 480),
    ]
    for box in answer_boxes:
        cv2.rectangle(img, (box[0], box[1]), (box[2], box[3]), (0, 0, 0), 5)
    buf = BytesIO()
    from PIL import Image

    Image.fromarray(img).save(buf, format="PNG")
    return buf.getvalue()


def test_detect_regions_and_answers():
    payload = _make_template()
    regions = detect_question_regions(payload)
    answers = detect_answer_boxes(payload)
    assert len(regions) >= 3
    assert len(answers) >= 3


@pytest.mark.asyncio
async def test_extract_template_regions_association():
    payload = _make_template()

    async def fake_ocr(*_args, **_kwargs):
        return {"text": "Q1"}

    regions, warnings = await extract_template_regions(payload, fake_ocr)
    assert len(regions) >= 3
    assert all(region.answer_box for region in regions)
    assert warnings == []


@pytest.mark.asyncio
async def test_extract_template_regions_missing_regions():
    img = np.full((500, 500, 3), 255, dtype=np.uint8)
    buf = BytesIO()
    from PIL import Image

    Image.fromarray(img).save(buf, format="PNG")

    async def fake_ocr(*_args, **_kwargs):
        return {"text": "Q1"}

    with pytest.raises(ValueError):
        await extract_template_regions(buf.getvalue(), fake_ocr)


@pytest.mark.asyncio
async def test_extract_template_regions_fallback_from_answers():
    payload = _make_boxes_only()

    async def fake_ocr(*_args, **_kwargs):
        return {"text": "Q1"}

    regions, warnings = await extract_template_regions(payload, fake_ocr)
    assert len(regions) == 2
    assert any(w.get("code") == "REGIONS_FALLBACK_FROM_ANSWER_BOXES" for w in warnings)


@pytest.mark.asyncio
async def test_extract_template_regions_multiple_answer_boxes():
    payload = _make_template_with_double_answer()

    async def fake_ocr(*_args, **_kwargs):
        return {"text": "Q1"}

    with pytest.raises(TemplateValidationError) as exc:
        await extract_template_regions(payload, fake_ocr)

    assert exc.value.code == "MULTIPLE_ANSWER_BOXES"
    assert exc.value.detail["answer_boxes_count"] >= 2


def test_detect_regions_no_merge():
    img = np.full((900, 800, 3), 255, dtype=np.uint8)
    regions = [
        (60, 80, 740, 300),
        (60, 360, 740, 580),
    ]
    for rect in regions:
        _draw_dashed_rect(img, rect, thickness=2)
    buf = BytesIO()
    from PIL import Image

    Image.fromarray(img).save(buf, format="PNG")
    detected = detect_question_regions(buf.getvalue())
    assert len(detected) >= 2
