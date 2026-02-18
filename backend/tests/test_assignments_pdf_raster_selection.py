from io import BytesIO

from PIL import Image

from backend.api.assignments import _extract_master_key_image_bytes


def _png_bytes(width: int, height: int, color: tuple[int, int, int]) -> bytes:
    buf = BytesIO()
    Image.new("RGB", (width, height), color=color).save(buf, format="PNG")
    return buf.getvalue()


class _Reader:
    def __init__(self):
        self.pages = [object()]


def test_master_key_pdf_raster_selection_prefers_more_answer_boxes(monkeypatch):
    embedded_png = _png_bytes(1200, 1600, (200, 200, 200))
    rendered_png = _png_bytes(1800, 2400, (120, 120, 120))

    monkeypatch.setattr("backend.api.assignments.PdfReader", lambda *_args, **_kwargs: _Reader())

    def fake_extract(_payload, *, page_index: int, dpi: float, prefer: str = "auto"):
        assert page_index == 0
        assert dpi > 0
        if prefer == "embedded":
            return embedded_png, "largest_embedded_raster"
        return rendered_png, "rendered_page"

    monkeypatch.setattr("backend.api.assignments.extract_pdf_page_raster_png", fake_extract)

    def fake_detect(image_bytes: bytes):
        if image_bytes == embedded_png:
            return [(1.0, 1.0, 10.0, 10.0)] * 4
        if image_bytes == rendered_png:
            return [(1.0, 1.0, 10.0, 10.0)] * 9
        return []

    monkeypatch.setattr("backend.api.assignments.detect_answer_boxes", fake_detect)

    out = _extract_master_key_image_bytes(b"%PDF-1.4 fake", ".pdf", "application/pdf")
    assert out == rendered_png
