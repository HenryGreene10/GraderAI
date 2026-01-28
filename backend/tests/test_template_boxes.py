from io import BytesIO

from PIL import Image, ImageDraw

from backend.services.template import detect_answer_boxes


def _make_template() -> bytes:
    image = Image.new("RGB", (800, 1000), color=(255, 255, 255))
    draw = ImageDraw.Draw(image)
    boxes = [
        (80, 80, 360, 200),
        (420, 80, 720, 200),
        (80, 260, 360, 380),
    ]
    for box in boxes:
        draw.rectangle(box, outline=(0, 0, 0), width=6)
    buf = BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def test_detect_answer_boxes_basic():
    payload = _make_template()
    boxes = detect_answer_boxes(payload)
    assert len(boxes) >= 3
