from io import BytesIO
from types import SimpleNamespace

from PIL import Image

from backend.services import pdf_raster


def _png_bytes(width: int, height: int, color: tuple[int, int, int]) -> bytes:
    buf = BytesIO()
    Image.new("RGB", (width, height), color=color).save(buf, format="PNG")
    return buf.getvalue()


class _FakePage:
    def __init__(self, *, width_pt: float, height_pt: float, images: list[object], rotate: int = 0):
        self.mediabox = SimpleNamespace(width=width_pt, height=height_pt)
        self.images = images
        self._rotate = rotate

    def get(self, key: str):
        if key == "/Rotate":
            return self._rotate
        return None


class _FakeReader:
    def __init__(self, pages: list[object]):
        self.pages = pages


def test_extract_pdf_page_raster_png_prefers_largest_pixel_area(monkeypatch):
    small = SimpleNamespace(data=_png_bytes(120, 90, (200, 10, 10)))
    large = SimpleNamespace(data=_png_bytes(640, 480, (10, 200, 10)))
    page = _FakePage(width_pt=612, height_pt=792, images=[small, large], rotate=0)

    monkeypatch.setattr(pdf_raster, "PdfReader", lambda *_args, **_kwargs: _FakeReader([page]))

    png, source = pdf_raster.extract_pdf_page_raster_png(b"fake-pdf", page_index=0, dpi=300)
    assert source == "largest_embedded_raster"
    with Image.open(BytesIO(png)) as img:
        assert (img.width, img.height) == (640, 480)


def test_extract_pdf_page_raster_png_applies_page_rotation_to_embedded_raster(monkeypatch):
    image = SimpleNamespace(data=_png_bytes(300, 120, (10, 10, 200)))
    page = _FakePage(width_pt=612, height_pt=792, images=[image], rotate=90)

    monkeypatch.setattr(pdf_raster, "PdfReader", lambda *_args, **_kwargs: _FakeReader([page]))

    png, source = pdf_raster.extract_pdf_page_raster_png(b"fake-pdf", page_index=0, dpi=300)
    assert source == "largest_embedded_raster"
    with Image.open(BytesIO(png)) as img:
        assert (img.width, img.height) == (120, 300)


def test_extract_pdf_page_raster_png_renders_or_falls_back_to_fixed_dpi_size(monkeypatch):
    page = _FakePage(width_pt=72, height_pt=144, images=[], rotate=0)
    monkeypatch.setattr(pdf_raster, "PdfReader", lambda *_args, **_kwargs: _FakeReader([page]))

    def fake_render(_payload, *, page_index: int, target_size: tuple[int, int], rotation_deg: int):
        assert page_index == 0
        assert rotation_deg == 0
        assert target_size == (100, 200)
        return _png_bytes(target_size[0], target_size[1], (50, 50, 50))

    monkeypatch.setattr(pdf_raster, "_render_page_with_pillow", fake_render)

    rendered_png, rendered_source = pdf_raster.extract_pdf_page_raster_png(b"fake-pdf", page_index=0, dpi=100)
    assert rendered_source == "rendered_page"
    with Image.open(BytesIO(rendered_png)) as img:
        assert (img.width, img.height) == (100, 200)

    monkeypatch.setattr(pdf_raster, "_render_page_with_pillow", lambda *_args, **_kwargs: None)
    blank_png, blank_source = pdf_raster.extract_pdf_page_raster_png(b"fake-pdf", page_index=0, dpi=100)
    assert blank_source == "blank_page_fallback"
    with Image.open(BytesIO(blank_png)) as img:
        assert (img.width, img.height) == (100, 200)
