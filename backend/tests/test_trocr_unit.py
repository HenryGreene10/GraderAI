import io
import importlib

from PIL import Image


def test_normalize_preserves_newlines():
    mod = importlib.import_module("backend.ocr.run_ocr")
    s = "  hello\r\nworld  "
    out = mod.normalize(s)
    assert out == "hello\nworld"


def test_bytes_to_image_jpg(monkeypatch):
    tl = importlib.import_module("backend.ocr.providers.trocr_local")
    # make a tiny 2x1 white image
    img = Image.new("RGB", (2, 1), (255, 255, 255))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    b = buf.getvalue()

    out = tl._bytes_to_image(b, "sample.jpg")
    assert isinstance(out, Image.Image)
    assert out.size == (2, 1)


def test_bytes_to_image_pdf(monkeypatch):
    tl = importlib.import_module("backend.ocr.providers.trocr_local")

    class FakePixmap:
        width = 3
        height = 2
        samples = (b"\xff\xff\xff" * (3 * 2))

    class FakePage:
        def load_page(self, idx):
            return self
        def get_pixmap(self, dpi=200):
            return FakePixmap()

    class FakeDoc:
        def __enter__(self):
            return FakePage()
        def __exit__(self, *args):
            return False

    def fake_open(stream, filetype):
        return FakeDoc()

    monkeypatch.setattr(tl, "fitz", type("F", (), {"open": staticmethod(fake_open)}))

    out = tl._bytes_to_image(b"%PDF-1.4\n...", "page.pdf")
    assert isinstance(out, Image.Image)
    assert out.size == (3, 2)

