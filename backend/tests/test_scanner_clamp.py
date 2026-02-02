from io import BytesIO

from PIL import Image
from pypdf import PdfReader

from backend.services import scanner


def _make_image(width: int, height: int) -> bytes:
    img = Image.new("RGB", (width, height), color=(240, 240, 240))
    buf = BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def test_normalize_clamps_dimensions(monkeypatch):
    monkeypatch.setattr(scanner, "_HAS_CV2", False)
    monkeypatch.setattr(scanner, "_CV2_ERR", "forced")

    small = _make_image(10, 10)
    result = scanner.normalize_image_bytes(small)
    assert result.width_px >= scanner.MIN_DIM_PX
    assert result.height_px >= scanner.MIN_DIM_PX

    big = _make_image(8000, 3000)
    result = scanner.normalize_image_bytes(big)
    assert max(result.width_px, result.height_px) <= scanner.MAX_DIM_PX
    img = Image.open(BytesIO(result.normalized_png))
    assert img.size == (result.width_px, result.height_px)


def test_pdf_page_size_clamped(monkeypatch):
    monkeypatch.setattr(scanner, "_HAS_CV2", False)
    monkeypatch.setattr(scanner, "_CV2_ERR", "forced")

    big = _make_image(8000, 8000)
    result = scanner.normalize_image_bytes(big)
    reader = PdfReader(BytesIO(result.normalized_pdf))
    page = reader.pages[0]
    width_in = float(page.mediabox.width) / 72.0
    height_in = float(page.mediabox.height) / 72.0
    assert width_in <= scanner.MAX_PDF_IN + 0.02
    assert height_in <= scanner.MAX_PDF_IN + 0.02
