from backend.models.schemas import Overlay, OverlayMark
from backend.services import report as report_mod


class _CanvasSpy:
    def __init__(self):
        self.rect_calls = []
        self.draw_calls = []

    def setFont(self, *_args, **_kwargs):
        return None

    def setFillColorRGB(self, *_args, **_kwargs):
        return None

    def setStrokeColorRGB(self, *_args, **_kwargs):
        return None

    def setLineWidth(self, *_args, **_kwargs):
        return None

    def rect(self, x, y, w, h, **_kwargs):
        self.rect_calls.append((x, y, w, h))

    def drawString(self, x, y, text):
        self.draw_calls.append((x, y, text))


def test_draw_marks_converts_px_to_pdf(monkeypatch):
    calls = {"px": [], "rect": []}

    def fake_px_to_pdf(x, y, norm, page):
        calls["px"].append((x, y, norm, page))
        return (10.0, 20.0)

    def fake_rect_px_to_pdf(rect, norm, page):
        calls["rect"].append((tuple(rect), norm, page))
        return (1.0, 2.0, 3.0, 4.0)

    monkeypatch.setattr(report_mod, "px_to_pdf", fake_px_to_pdf)
    monkeypatch.setattr(report_mod, "rect_px_to_pdf", fake_rect_px_to_pdf)

    overlay = Overlay(
        page=1,
        marks=[
            OverlayMark(tool="note", coords=[100.0, 200.0], text="hi"),
            OverlayMark(tool="bubble", coords=[300.0, 400.0, 50.0, 60.0], text="Score: 1/1"),
        ],
    )

    canvas = _CanvasSpy()
    report_mod._draw_marks(
        canvas,
        overlay,
        normalized_size_px=(3000.0, 4000.0),
        page_size_pt=(612.0, 792.0),
    )

    assert calls["px"]
    assert calls["rect"]
    assert (10.0, 20.0, "hi") in canvas.draw_calls
    assert (1.0, 2.0, 3.0, 4.0) in canvas.rect_calls
